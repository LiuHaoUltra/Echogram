from telegram import Update, constants
from telegram.ext import ContextTypes
from openai import AsyncOpenAI

from core.access_service import access_service
from core.history_service import history_service
from core.config_service import config_service
from core.secure import is_admin
from utils.logger import logger
from utils.prompts import prompt_builder

async def process_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    核心消息处理函数
    1. 白名单检查
    2. 历史记录读取
    3. 调用 LLM
    4. 回复并保存
    """
    user = update.effective_user
    chat = update.effective_chat
    message = update.message
    
    if not message or not message.text:
        return

    # --- 1. 访问控制 ---
    # 允许 Admin 私聊，或白名单内的 Chat
    allowed = False
    is_adm = is_admin(user.id)
    
    logger.info(f"MSG [{chat.id}] from {user.first_name}: {message.text[:20]}...")

    if chat.type == constants.ChatType.PRIVATE and is_adm:
        allowed = True
    else:
        # 检查是否在白名单
        if await access_service.is_whitelisted(chat.id):
            allowed = True
            
    if not allowed:
        # 静默，不回复
        logger.info(f"Access Denied for Chat ID: {chat.id}")
        return

    # --- 2. 准备上下文 ---
    # 检查引用
    reply_to_id = None
    reply_to_content = None
    
    if message.reply_to_message:
        reply_to_id = message.reply_to_message.message_id
        # 提取引用内容 (需要防止过长)
        raw_ref_text = message.reply_to_message.text or "[Non-text message]"
        reply_to_content = (raw_ref_text[:30] + "..") if len(raw_ref_text) > 30 else raw_ref_text

    # 保存用户消息 (带 ID 和 引用信息)
    await history_service.add_message(
        chat.id, 
        "user", 
        message.text, 
        message_id=message.message_id,
        reply_to_id=reply_to_id,
        reply_to_content=reply_to_content
    )
    
    # 获取配置
    configs = await config_service.get_all_settings()
    api_key = configs.get("api_key")
    base_url = configs.get("api_base_url")
    model = configs.get("model_name", "gpt-3.5-turbo")
    system_prompt_custom = configs.get("system_prompt")
    timezone = configs.get("timezone", "UTC")
    
    if not api_key:
        if is_adm:
            await message.reply_text("⚠️ 尚未配置 API Key，请使用 /dashboard 配置。")
        return

    # 组装 System Prompt (注入时间与时区)
    system_content = prompt_builder.build_system_prompt(system_prompt_custom, timezone=timezone)
    
    # 获取历史记录 (Rolling Context)
    # 取最近 10 条，避免 Token 溢出
    history_msgs = await history_service.get_recent_context(chat.id, limit=10)
    
    # 构造 OpenAI Messages
    # 格式化历史消息：[MSG ID] [TIME] content
    messages = [{"role": "system", "content": system_content}]
    
    # 时区处理工具
    import pytz
    from datetime import datetime
    try:
        tz = pytz.timezone(timezone)
    except:
        tz = pytz.UTC
        
    for h in history_msgs:
        # 将 timestamp 转为对应时区
        if h.timestamp:
            # 数据库存的是 UTC (默认)，需要转换
            # 假设 h.timestamp 是 naive datetime (UTC)
            utc_time = h.timestamp.replace(tzinfo=pytz.UTC)
            local_time = utc_time.astimezone(tz)
            time_str = local_time.strftime("%Y-%m-%d %H:%M:%S")
        else:
            time_str = "Unknown Time"
            
        # 注入 Message ID 和 Timestamp
        # 策略变更：仅 User 消息添加元数据前缀，避免 Assistant 复读
        
        if h.role == 'user':
            prefix = f"[MSG {h.message_id}] [{time_str}] " if h.message_id else f"[MSG ?] [{time_str}] "
            if h.reply_to_content:
                prefix += f'(Reply to "{h.reply_to_content}") '
            messages.append({"role": "user", "content": prefix + h.content})
        elif h.role == 'system':
            # System Info (如 Reaction 通知)
            # 保持 system 角色，或者作为 user 角色的一种特殊形式？
            # 为了让 LLM 明确这不是它自己说的，system 是合理的。
            # 但为了防止 system message 过多导致注意力分散，加上时间戳有助于定位。
            messages.append({"role": "system", "content": f"[{time_str}] {h.content}"})
        else:
            # Assistant 消息：仅展示内容，甚至不展示时间，防止 LLM confusion
            messages.append({"role": "assistant", "content": h.content})
        
    # --- 3. 调用 API ---
    # 发送 "正在输入..." 状态
    await context.bot.send_chat_action(chat_id=chat.id, action=constants.ChatAction.TYPING)
    
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

        # 防御性清洗：移除所有系统元数据
        import re
        # 1. 移除行首的连续方括号块 (元数据前缀)
        reply_content = re.sub(r"^(\s*\[[^\]]+\])+", "", reply_content).strip()
        
        # 2. 及其顽固的情况：如果并没有在行首，但也包含了 [MSG ...] (防止泄漏)
        reply_content = re.sub(r"\[MSG\s*[^\]]+\]", "", reply_content)
        # 移除时间戳
        reply_content = re.sub(r"\[\d{4}-\d{2}-\d{2}.*?\]", "", reply_content)
        
        # 3. 修复转义换行符 (LLM 经常输出 \\n)
        reply_content = reply_content.replace("\\n", "\n")
        
        reply_content = reply_content.strip()
        logger.info(f"CLEANED OUTPUT: {reply_content!r}")

        if not reply_content:
             reply_content = "..." 

        # --- 4. 回复用户 (支持拆分 & 引用 & 表情) ---
        from utils.splitter import split_message
        
        # 4.1 解析全局指令 (如 React)
        # React 指令通常在开头或结尾
        react_emoji = None
        # 4.1 解析全局指令 (如 React)
        # React 指令通常在开头或结尾
        # 支持格式: \React:Emoji 或 \React:Emoji:MsgID
        # 正则需要更严谨，Emoji 可能是 unicode 字符
        react_emoji = None
        react_target_id = None
        
        # 匹配 \React:Emoji(:MsgID)?
        # 组1: Emoji (非空白字符，排除冒号)
        # 组2: 可选的 :MsgID
        react_match = re.search(r"(?:\\|/)?React[:\s]+([^:\s]+)(?::(\d+))?", reply_content, re.IGNORECASE)
        
        if react_match:
            try:
                react_emoji = react_match.group(1)
                if react_match.group(2):
                    react_target_id = int(react_match.group(2))
                else:
                    react_target_id = message.message_id # 默认：当前用户消息
                    
                # 必须移除指令
                reply_content = reply_content.replace(react_match.group(0), "").strip()
            except:
                pass

        # 如果有 React，先执行 React
        if react_emoji:
            try:
                from telegram import ReactionTypeEmoji
                # 对指定消息进行 React
                await context.bot.set_message_reaction(
                    chat_id=chat.id,
                    message_id=react_target_id,
                    reaction=[ReactionTypeEmoji(react_emoji)]
                )
            except Exception as e:
                logger.warning(f"Failed to set reaction {react_emoji} to {react_target_id}: {e}")
        
        # 4.2 处理文本回复
        if not reply_content and react_emoji:
            return # 仅表情

        if not reply_content:
            reply_content = "..."

        # 先拆分消息
        reply_parts = split_message(reply_content)
        
        for part in reply_parts:
            # 解析每段消息的 Replay 标记
            # 查找 \Replay:123 或 \Reply:123
            target_id = None
            clean_part = part
            
            match = re.search(r"(?:\\|/)?Repla?y[:\s]+(\d+)", part, re.IGNORECASE)
            if match:
                try:
                    target_id = int(match.group(1))
                    # 移除指令
                    clean_part = part.replace(match.group(0), "").strip()
                except:
                    pass
            
            if not clean_part:
                continue

            try:
                if target_id:
                    # 尝试引用特定消息
                    await chat.send_message(clean_part, reply_to_message_id=target_id)
                else:
                    # 正常发送，不引用任何消息
                    await chat.send_message(clean_part)
            except Exception as e:
                logger.warning(f"Failed to send message (ref={target_id}): {e}")
                # Fallback: 发送普通消息，忽略引用失败
                try:
                    await chat.send_message(clean_part)
                except Exception as e2:
                    logger.error(f"Fallback send failed: {e2}")
        
        # 保存 AI 回复
        await history_service.add_message(chat.id, "assistant", reply_content)

    except Exception as e:
        logger.error(f"API Call failed: {e}")
        # 仅通知 Admin 错误信息
        if is_adm and chat.type == constants.ChatType.PRIVATE:
            await message.reply_text(f"❌ API 调用失败: {e}")

async def process_reaction_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    处理表情回应更新
    只记录 User -> Bot 消息的 Reaction，或者 User -> User (在群组中)
    作为 System Message 存入历史
    """
    reaction = update.message_reaction
    if not reaction:
        return
        
    chat = reaction.chat
    user = reaction.user
    message_id = reaction.message_id
    
    # 忽略 Bot 自己的 Reaction (避免死循环或冗余)
    if user and user.id == context.bot.id:
        return

    # 获取 Reaction 内容
    emojis = []
    for react in reaction.new_reaction:
        # Telegram ReactionTypeEmoji or ReactionTypeCustomEmoji
        if hasattr(react, 'emoji'):
            emojis.append(react.emoji)
        elif hasattr(react, 'custom_emoji_id'):
            emojis.append('[CustomEmoji]')
            
    if not emojis:
        # 可能是移除了表情
        content = f"[System Info] {user.first_name if user else 'User'} removed reaction from [MSG {message_id}]"
    else:
        emoji_str = "".join(emojis)
        content = f"[System Info] {user.first_name if user else 'User'} reacted {emoji_str} to [MSG {message_id}]"

    logger.info(f"REACTION [{chat.id}]: {content}")
    
    # 存入 History (System Role)
    await history_service.add_message(
        chat_id=chat.id,
        role="system",
        content=content
    )
