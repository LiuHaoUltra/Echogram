from telegram import Update, constants
from telegram.ext import ContextTypes
from openai import AsyncOpenAI
import re

from core.access_service import access_service
from core.history_service import history_service
from core.config_service import config_service
from core.summary_service import summary_service
from config.settings import settings
from core.secure import is_admin
from core.lazy_sender import lazy_sender
from utils.logger import logger
from utils.prompts import prompt_builder
import asyncio # Ensure asyncio is imported

# ... (process_message_entry remains unchanged) -> Restoring logic
async def process_message_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    HTTP/Telegram 消息入口
    1. 鉴权
    2. 存入历史
    3. 放入缓冲队列 (LazySender)
    """
    user = update.effective_user
    chat = update.effective_chat
    message = update.message
    
    if not message or not message.text:
        return
        
    # 指令交由 CommandHandler 处理
    if message.text.strip().startswith('/'):
        return

    # --- 1. 访问控制 ---
    is_adm = is_admin(user.id)
    
    if chat.type == constants.ChatType.PRIVATE:
        # 私聊：仅管理员可见，但不作为聊天记录处理
        if is_adm:
            # 可以在此处通过 /dashboard 管理，这里不做消息响应
            pass
        return
    else:
        # 群组：必须在白名单内
        if not await access_service.is_whitelisted(chat.id):
            return
            
    # 通过鉴权后记录日志
    logger.info(f"MSG [{chat.id}] from {user.first_name}: {message.text[:20]}...")

    # 存入历史
    # 检查引用
    reply_to_id = None
    reply_to_content = None
    
    if message.reply_to_message:
        reply_to_id = message.reply_to_message.message_id
        # 提取引用内容 (需要防止过长)
        raw_ref_text = message.reply_to_message.text or "[Non-text message]"
        reply_to_content = (raw_ref_text[:30] + "..") if len(raw_ref_text) > 30 else raw_ref_text

    # 保存用户消息
    await history_service.add_message(
        chat.id, 
        "user", 
        message.text, 
        message_id=message.message_id,
        reply_to_id=reply_to_id,
        reply_to_content=reply_to_content
    )
    
    # 通过 LazySender 防抖触发
    await lazy_sender.on_message(chat.id, context)

    # 主动触发总结检查 (确保在 AI 回复前尽可能完成总结)
    try:
        asyncio.create_task(summary_service.check_and_summarize(chat.id))
    except Exception as e:
        logger.error(f"Failed to trigger proactive summary: {e}")

async def generate_response(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """
    核心回复生成逻辑 (LazySender 回调)
    1. 读取历史
    2. 调用 LLM
    3. 发送回复
    """
    logger.info(f"Generate Response triggered for Chat {chat_id}")
    
    # 获取配置
    configs = await config_service.get_all_settings()
    api_key = configs.get("api_key")
    base_url = configs.get("api_base_url")
    model = configs.get("model_name", "gpt-3.5-turbo")
    system_prompt_custom = configs.get("system_prompt")
    timezone = configs.get("timezone", "UTC")

    
    if not api_key:
        await context.bot.send_message(chat_id, "⚠️ 尚未配置 API Key，请使用 /dashboard 配置。")
        return

    # 获取长期记忆摘要
    dynamic_summary = await summary_service.get_summary(chat_id)

    # 组装 System Prompt
    system_content = prompt_builder.build_system_prompt(
        system_prompt_custom, 
        timezone=timezone, 
        dynamic_summary=dynamic_summary
    )
    
    # 获取历史记录 (Token 控制)
    # 优先读取 DB 配置
    token_limit_str = configs.get("history_tokens")
    if token_limit_str and token_limit_str.isdigit():
        target_tokens = int(token_limit_str)
    else:
        target_tokens = settings.HISTORY_WINDOW_TOKENS
        
    history_msgs = await history_service.get_token_controlled_context(chat_id, target_tokens=target_tokens)
    
    # 构造消息列表
    messages = [{"role": "system", "content": system_content}]
    
    # 时区转换
    import pytz
    try:
        tz = pytz.timezone(timezone)
    except:
        tz = pytz.UTC
        
    for h in history_msgs:
        # 将 timestamp 转为对应时区
        if h.timestamp:
            try:
                if h.timestamp.tzinfo is None:
                    utc_time = h.timestamp.replace(tzinfo=pytz.UTC)
                else:
                    utc_time = h.timestamp
                local_time = utc_time.astimezone(tz)
                time_str = local_time.strftime("%Y-%m-%d %H:%M:%S")
            except:
                time_str = "Time Error"
        else:
            time_str = "Unknown Time"
            
        # 注入 Message ID 和 Timestamp
        if h.role == 'user':
            prefix = f"[MSG {h.message_id}] [{time_str}] " if h.message_id else f"[MSG ?] [{time_str}] "
            if h.reply_to_content:
                prefix += f'(Reply to "{h.reply_to_content}") '
            messages.append({"role": "user", "content": prefix + h.content})
        elif h.role == 'system':
            messages.append({"role": "system", "content": f"[{time_str}] {h.content}"})
        else:
            messages.append({"role": "assistant", "content": h.content})
        
    # 调用 API
    msg_count = len(messages)
    logger.debug(f"Calling LLM ({model}) with {msg_count} messages...")
    
    try:
        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.7
        )
        
        if not response.choices or not response.choices[0].message.content:
             reply_content = "" 
             logger.warning(f"LLM ({model}) returned EMPTY content. Choices: {len(response.choices) if response.choices else 0}")
        else:
             reply_content = response.choices[0].message.content.strip()
             
        logger.info(f"RAW LLM OUTPUT: {reply_content!r}")

        # 1. 响应隔离与指令解析 (Tag-Driven Protocol)
        # 提取所有 <chat> 标签及其属性/内容
        # 格式：<chat reply="123" react="👍">内容</chat>
        tag_pattern = r"<chat(?P<attrs>[^>]*)>(?P<content>.*?)</chat>"
        matches = list(re.finditer(tag_pattern, reply_content, flags=re.DOTALL))
        
        if not matches:
             logger.warning("Response Protocol Violation: No <chat> tags found in LLM output.")
             # 防御性处理：如果没有标签，尝试发送原始响应（或清洗后的）
             reply_blocks = [{"content": reply_content.strip(), "reply": None, "react": None}]
        else:
             reply_blocks = []
             for m in matches:
                 attrs_raw = m.group("attrs")
                 content = m.group("content").strip()
                 
                 # 解析属性 (reply="xxx" react="xxx")
                 reply_id = None
                 react_emoji = None
                 
                 reply_match = re.search(r'reply=["\'](\d+)["\']', attrs_raw)
                 if reply_match:
                     reply_id = int(reply_match.group(1))
                     
                 # 提取表情：支持单引号、双引号，或者直接是 Emoji
                 react_match = re.search(r'react=["\']([^"\']+)["\']', attrs_raw)
                 if react_match:
                     react_emoji = react_match.group(1).strip()
                 
                 if content or react_emoji:
                     reply_blocks.append({
                         "content": content if content else "...",
                         "reply": reply_id,
                         "react": react_emoji
                     })

        # 2. 回复发送逻辑
        TG_FREE_REACTIONS = {
            "👍", "👎", "❤️", "🔥", "🥰", "👏", "😁", "🤔", "🤯", "😱", 
            "🤬", "😢", "🎉", "🤩", "🤮", "💩", "🙏", "👌", "🕊️", "🤡", 
            "🥱", "🥴", "😍", "🐳", "❤️‍🔥", "🌚", "🌭", "💯", "🤣", "🍴", 
            "💔", "🤨", "😐", "🍓", "🍾", "💋", "🖕", "😈", "😴", "😭", 
            "🤓", "👻", "👨‍💻", "👀", "🎃", "🙈", "😇", "😨", "🤝", "✍️", 
            "🤗", "🫡", "🎅", "🎄", "☃️", "💅", "🤪", "🗿", "🆒", "💘", 
            "🙊", "🦄", "😘", "💊", "🙊", "😎", "👾", "🤷‍♂️", "🤷", "🤷‍♀️", "😡"
        }

        for i, block in enumerate(reply_blocks):
            content = block["content"]
            target_reply_id = block["reply"]
            target_react_emoji = block["react"]

            # --- A. 处理表情回应 (Reaction) ---
            if target_react_emoji:
                # 解析 EMOJI:ID 格式
                react_id = None
                react_emoji_part = target_react_emoji
                if ":" in target_react_emoji:
                    parts = target_react_emoji.split(":", 1)
                    react_emoji_part = parts[0].strip()
                    try:
                        react_id = int(parts[1].strip())
                    except:
                        pass

                if react_emoji_part in TG_FREE_REACTIONS:
                    try:
                        # 确定目标 ID
                        react_target_id = react_id # 优先使用显示指定的 ID
                        if not react_target_id:
                            react_target_id = target_reply_id # 其次使用回复目标的 ID
                        
                        if not react_target_id:
                            # 最后使用最后一条用户消息 ID
                            last_user_msg = next((m for m in reversed(history_msgs) if m.role == 'user'), None)
                            if last_user_msg:
                                react_target_id = last_user_msg.message_id
                        
                        if react_target_id:
                            from telegram import ReactionTypeEmoji
                            await context.bot.set_message_reaction(
                                chat_id=chat_id,
                                message_id=react_target_id,
                                reaction=[ReactionTypeEmoji(react_emoji_part)]
                            )
                    except Exception as e:
                        logger.warning(f"Failed to set reaction ({react_emoji_part}) on MSG {react_target_id}: {e}")
                else:
                    logger.warning(f"Reaction ignored: '{react_emoji_part}' not in whitelist.")

            # --- B. 处理消息发送 (Message) ---
            if not content or content == "...":
                # 如果只有 Reaction 没有正文
                continue

            # 拟人化延迟逻辑
            if i > 0:
                await asyncio.sleep(1.0) # 气泡间隔
            
            # 计算打字时长
            typing_duration = min(len(content) * 0.15, 3.0) # 上限 3 秒，防止过长等待
            
            await context.bot.send_chat_action(chat_id=chat_id, action=constants.ChatAction.TYPING)
            await asyncio.sleep(typing_duration)

            try:
                await context.bot.send_message(
                    chat_id=chat_id, 
                    text=content, 
                    reply_to_message_id=target_reply_id
                )
            except Exception as e:
                logger.warning(f"Failed to send message part {i}: {e}")
                # 最后的防御：不带引用重试
                if target_reply_id:
                    try:
                        await context.bot.send_message(chat_id=chat_id, text=content)
                    except:
                        pass
        
        # 保存 AI 回复
        await history_service.add_message(chat_id, "assistant", reply_content)
        
        # 触发后台总结
        try:
            asyncio.create_task(summary_service.check_and_summarize(chat_id))
        except Exception as e:
            logger.error(f"Failed to trigger summary task: {e}")

    except Exception as e:
        logger.error(f"API Call failed: {e}")
        # 仅通知 Admin
        if is_admin(chat_id) and chat_id > 0:
             await context.bot.send_message(chat_id=chat_id, text=f"❌ API 调用失败: {e}")

async def process_reaction_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    处理表情回应更新
    """
    reaction = update.message_reaction
    if not reaction:
        return
        
    chat = reaction.chat
    user = reaction.user
    message_id = reaction.message_id
    
    # [NEW] 访问控制：私聊或非白名单群组静默
    if chat.type == constants.ChatType.PRIVATE:
        return
    if not await access_service.is_whitelisted(chat.id):
        return
        
    if user and user.id == context.bot.id:
        return

    emojis = []
    for react in reaction.new_reaction:
        if hasattr(react, 'emoji'):
            emojis.append(react.emoji)
        elif hasattr(react, 'custom_emoji_id'):
            emojis.append('[CustomEmoji]')
            
    if not emojis:
        content = f"[System Info] {user.first_name if user else 'User'} removed reaction from [MSG {message_id}]"
    else:
        emoji_str = "".join(emojis)
        content = f"[System Info] {user.first_name if user else 'User'} reacted {emoji_str} to [MSG {message_id}]"

    logger.info(f"REACTION [{chat.id}]: {content}")
    
    await history_service.add_message(
        chat_id=chat.id,
        role="system",
        content=content
    )

    # 绑定 LazySender 回调
lazy_sender.set_callback(generate_response)
