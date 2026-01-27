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
    2. 存入 History (User 消息)
    3. 放入 LazySender 缓冲队列
    """
    user = update.effective_user
    chat = update.effective_chat
    message = update.message
    
    if not message or not message.text:
        return
        
    # 如果是指令 (以 / 开头)，直接返回，不走缓冲（由 CommandHandler 处理）
    if message.text.strip().startswith('/'):
        return

    # --- 1. 访问控制 ---
    allowed = False
    is_adm = is_admin(user.id)
    
    logger.info(f"MSG [{chat.id}] from {user.first_name}: {message.text[:20]}...")

    if chat.type == constants.ChatType.PRIVATE:
        # [NEW] 私聊仅用于管理
        # 严格鉴权：只有管理员能收到提示，其他人静默
        if is_admin(user.id):
             await message.reply_text("⚠️ 私聊仅用于配置管理，请在群组中使用本机器人。\n\n如需管理，请使用 /dashboard。")
        return
    else:
        # 检查是否在白名单
        if await access_service.is_whitelisted(chat.id):
            allowed = True
            
    if not allowed:
        # 静默，不回复
        logger.info(f"Access Denied for Chat ID: {chat.id}")
        return

    # --- 2. 存入历史 ---
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
    
    # --- 3. 触发延迟发送 ---
    # 将任务交给 LazySender，它会在防抖结束后调用 generate_response
    await lazy_sender.on_message(chat.id, context)

async def generate_response(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """
    核心回复生成逻辑 (将被 LazySender 回调)
    1. 读取 History (包含刚刚缓冲的消息)
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

    # [NEW] 获取长期记忆摘要
    dynamic_summary = await summary_service.get_summary(chat_id)

    # 组装 System Prompt (注入时间与时区 + Summary)
    system_content = prompt_builder.build_system_prompt(
        system_prompt_custom, 
        timezone=timezone, 
        dynamic_summary=dynamic_summary
    )
    
    # [NEW] 获取历史记录 (Token Controlled)
    # 优先读取 DB 配置，没有则使用 Settings 默认值
    token_limit_str = configs.get("history_tokens")
    if token_limit_str and token_limit_str.isdigit():
        target_tokens = int(token_limit_str)
    else:
        target_tokens = settings.HISTORY_WINDOW_TOKENS
        
    history_msgs = await history_service.get_token_controlled_context(chat_id, target_tokens=target_tokens)
    
    # 构造 OpenAI Messages
    messages = [{"role": "system", "content": system_content}]
    
    # 时区处理工具
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
        
    # --- 3. 调用 API ---
    # [Removal] 移除预先的正在输入状态，改为在生成后根据字数模拟
    # await context.bot.send_chat_action(chat_id=chat_id, action=constants.ChatAction.TYPING)
    
    try:
        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.7
        )
        
        if not response.choices or not response.choices[0].message.content:
             reply_content = "..." 
             logger.warning("LLM returned empty content.")
        else:
             reply_content = response.choices[0].message.content.strip()
             
        logger.info(f"RAW LLM OUTPUT: {reply_content!r}")

        # [NEW] 响应隔离处理
        # 1. 优先尝试提取 <chat>...</chat>
        chat_match = re.search(r"<chat>(.*?)</chat>", reply_content, flags=re.DOTALL)
        if chat_match:
            reply_content = chat_match.group(1).strip()
        else:
            # 如果未找到 <chat> 标签，记录警告，但为了防止丢消息，暂且保留原始内容
            # 信任 Prompt 指令已足够强
            logger.warning("Response Protocol Violation: No <chat> tags found in LLM output.")

        # 防御性清洗 (System tags)

        # 防御性清洗
        reply_content = re.sub(r"^(\s*\[[^\]]+\])+", "", reply_content).strip()
        reply_content = re.sub(r"\[MSG\s*[^\]]+\]", "", reply_content)
        reply_content = re.sub(r"\[\d{4}-\d{2}-\d{2}.*?\]", "", reply_content)
        reply_content = reply_content.replace("\\n", "\n")
        reply_content = reply_content.strip()

        if not reply_content:
             reply_content = "..." 

        # --- 4. 回复用户 ---
        from utils.splitter import split_message
        
        # 4.1 解析 React
        react_emoji = None
        react_target_id = None
        
        react_match = re.search(r"(?:\\|/)?React[:\s]+([^:\s]+)(?::(\d+))?", reply_content, re.IGNORECASE)
        
        if react_match:
            try:
                react_emoji = react_match.group(1)
                if react_match.group(2):
                    react_target_id = int(react_match.group(2))
                else:
                    # 如果没有指定 Target ID，默认对“最后一条用户消息”React
                    last_user_msg = next((m for m in reversed(history_msgs) if m.role == 'user'), None)
                    if last_user_msg:
                        react_target_id = last_user_msg.message_id
                    
                reply_content = reply_content.replace(react_match.group(0), "").strip()
            except:
                pass

        if react_emoji and react_target_id:
            try:
                from telegram import ReactionTypeEmoji
                await context.bot.set_message_reaction(
                    chat_id=chat_id,
                    message_id=react_target_id,
                    reaction=[ReactionTypeEmoji(react_emoji)]
                )
            except Exception as e:
                logger.warning(f"Failed to set reaction: {e}")
        
        if not reply_content and react_emoji:
            return 

        if not reply_content:
            reply_content = "..."

        reply_parts = split_message(reply_content)
        
        for i, part in enumerate(reply_parts):
            target_id = None
            clean_part = part
            
            match = re.search(r"(?:\\|/)?Repla?y[:\s]+(\d+)", part, re.IGNORECASE)
            if match:
                try:
                    target_id = int(match.group(1))
                    clean_part = part.replace(match.group(0), "").strip()
                except:
                    pass
            
            if not clean_part:
                continue

            # [NEW] 拟人化打字延迟逻辑
            
            # 1. 多条消息之间的间隔 (1秒)
            if i > 0:
                await asyncio.sleep(1.0)
            
            # 2. 计算打字时间
            # 规则：未包含命令的纯文本长度，每个字 0.2 秒
            typing_duration = len(clean_part) * 0.2
            
            # 发送 Typing 状态
            await context.bot.send_chat_action(chat_id=chat_id, action=constants.ChatAction.TYPING)
            
            # 等待模拟打字
            # 注意：Telegram Typing status 持续 5s，如果 duration > 5s，它会消失。
            # 为了更真实，这里可以每 4.5s 补发一次，但为了简洁，暂且只发一次。
            await asyncio.sleep(typing_duration)

            try:
                if target_id:
                    await context.bot.send_message(chat_id=chat_id, text=clean_part, reply_to_message_id=target_id)
                else:
                    await context.bot.send_message(chat_id=chat_id, text=clean_part)
            except Exception as e:
                logger.warning(f"Failed to send message: {e}")
                try:
                     await context.bot.send_message(chat_id=chat_id, text=clean_part)
                except:
                    pass
        
        # 保存 AI 回复
        await history_service.add_message(chat_id, "assistant", reply_content)
        
        # [NEW] 触发后台总结任务 (Fire-and-Forget)
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
    
    # [NEW] 私聊静默：不记录 Reaction
    if chat.type == constants.ChatType.PRIVATE:
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
