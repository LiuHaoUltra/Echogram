from telegram import Update, constants
from telegram.ext import ContextTypes
from openai import AsyncOpenAI
import re
import asyncio
import pytz

from core.access_service import access_service
from core.history_service import history_service
from core.config_service import config_service
from core.summary_service import summary_service
from config.settings import settings
from core.secure import is_admin
from core.lazy_sender import lazy_sender
from utils.logger import logger
from utils.prompts import prompt_builder
from utils.config_validator import safe_int_config, safe_float_config
from core.sender_service import sender_service

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
    
    # 空值检查：user、chat、message 必须存在
    if not user or not chat or not message or not message.text:
        return
        
    # 指令交由 CommandHandler 处理
    if message.text.strip().startswith('/'):
        return

    # --- 1. 访问控制 ---
    is_adm = is_admin(user.id)
    
    if chat.type == constants.ChatType.PRIVATE:
        # 私聊：仅管理员可见，但不作为聊天记录处理
        if is_adm:
            pass
        return
    else:
        # 群组：必须在白名单内
        if not await access_service.is_whitelisted(chat.id):
            return
            
    # 通过鉴权后记录日志
    logger.info(f"MSG [{chat.id}] from {user.first_name}: {message.text[:20]}...")

    # 存入历史
    reply_to_id = None
    reply_to_content = None
    
    if message.reply_to_message:
        reply_to_id = message.reply_to_message.message_id
        raw_ref_text = message.reply_to_message.text or "[Non-text message]"
        reply_to_content = (raw_ref_text[:30] + "..") if len(raw_ref_text) > 30 else raw_ref_text

    await history_service.add_message(
        chat.id, 
        "user", 
        message.text, 
        message_id=message.message_id,
        reply_to_id=reply_to_id,
        reply_to_content=reply_to_content
    )
    
    await lazy_sender.on_message(chat.id, context)

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
    
    configs = await config_service.get_all_settings()
    api_key = configs.get("api_key")
    base_url = configs.get("api_base_url")
    model = configs.get("model_name", "gpt-3.5-turbo")
    system_prompt_custom = configs.get("system_prompt")
    timezone = configs.get("timezone", "UTC")

    if not api_key:
        await context.bot.send_message(chat_id, "⚠️ 尚未配置 API Key，请使用 /dashboard 配置。")
        return

    dynamic_summary = await summary_service.get_summary(chat_id)

    system_content = prompt_builder.build_system_prompt(
        system_prompt_custom, 
        timezone=timezone, 
        dynamic_summary=dynamic_summary
    )
    
    # 安全转换配置值，范围 100-50000
    target_tokens = safe_int_config(
        configs.get("history_tokens"),
        settings.HISTORY_WINDOW_TOKENS,
        min_val=100,
        max_val=50000
    )
        
    history_msgs = await history_service.get_token_controlled_context(chat_id, target_tokens=target_tokens)
    
    messages = [{"role": "system", "content": system_content}]
    
    # 安全的时区处理
    try:
        tz = pytz.timezone(timezone)
    except pytz.UnknownTimeZoneError:
        logger.warning(f"Unknown timezone '{timezone}', fallback to UTC")
        tz = pytz.UTC
        
    for h in history_msgs:
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
            
        if h.role == 'user':
            prefix = f"[MSG {h.message_id}] [{time_str}] " if h.message_id else f"[MSG ?] [{time_str}] "
            if h.reply_to_content:
                prefix += f'(Reply to "{h.reply_to_content}") '
            messages.append({"role": "user", "content": prefix + h.content})
        elif h.role == 'system':
            messages.append({"role": "system", "content": f"[{time_str}] {h.content}"})
        else:
            messages.append({"role": "assistant", "content": h.content})
        
    # 安全转换 temperature，范围 0.0-2.0
    current_temp = safe_float_config(
        configs.get("temperature", "0.7"),
        default=0.7,
        min_val=0.0,
        max_val=2.0
    )

    try:
        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=current_temp,
            max_tokens=4000
        )
        
        # 检查 LLM 响应有效性
        if not response.choices or not response.choices[0].message.content:
            logger.warning(f"LLM ({model}) returned EMPTY content.")
            await context.bot.send_message(
                chat_id=chat_id,
                text="⚠️ AI 未返回有效回复，请稍后重试或检查配置"
            )
            return
        
        reply_content = response.choices[0].message.content.strip()
        logger.info(f"RAW LLM OUTPUT: {reply_content!r}")

        await sender_service.send_llm_reply(
            chat_id=chat_id,
            reply_content=reply_content,
            context=context,
            history_msgs=history_msgs
        )

    except Exception as e:
        logger.error(f"API Call failed: {e}")
        if is_admin(chat_id) and chat_id > 0:
             await context.bot.send_message(chat_id=chat_id, text=f"❌ API 调用失败: {e}")

async def process_reaction_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理表情回应更新"""
    reaction = update.message_reaction
    if not reaction:
        return
        
    chat = reaction.chat
    user = reaction.user
    message_id = reaction.message_id
    
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

lazy_sender.set_callback(generate_response)
