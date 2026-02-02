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
from core.voice_service import voice_service, ASRNotConfiguredError, TTSNotConfiguredError, VoiceServiceError
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

async def process_voice_message_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    语音消息入口
    1. 鉴权
    2. 下载语音 → ASR 转文字
    3. 存入历史 (message_type=voice)
    4. 触发回复
    """
    user = update.effective_user
    chat = update.effective_chat
    message = update.message
    
    # 空值检查
    if not user or not chat or not message or not message.voice:
        return
    
    # --- 1. 访问控制 ---
    is_adm = is_admin(user.id)
    
    if chat.type == constants.ChatType.PRIVATE:
        # 私聊：仅用于管理功能，不作为聊天记录处理
        if is_adm:
            pass
        return
    else:
        # 群组：必须在白名单内
        if not await access_service.is_whitelisted(chat.id):
            return
    
    logger.info(f"VOICE [{chat.id}] from {user.first_name}: {message.voice.duration}s")
    
    # --- 2. 下载语音并 ASR ---
    try:
        voice = message.voice
        file = await context.bot.get_file(voice.file_id)
        voice_bytes = await file.download_as_bytearray()
        
        # ASR 转文字
        transcribed_text = await voice_service.speech_to_text(bytes(voice_bytes))
        
        if not transcribed_text:
            await context.bot.send_message(
                chat_id=chat.id,
                text="⚠️ 语音识别失败，请重试"
            )
            return
        
        logger.info(f"ASR Result: {transcribed_text[:50]}...")
        
    except ASRNotConfiguredError:
        await context.bot.send_message(
            chat_id=chat.id,
            text="⚠️ 语音识别功能未配置\n\n请管理员发送 /dashboard 到私聊完成 ASR 模型配置"
        )
        return
    except VoiceServiceError as e:
        logger.error(f"Voice ASR failed: {e}")
        await context.bot.send_message(
            chat_id=chat.id,
            text=f"⚠️ 语音识别失败: {str(e)}"
        )
        return
    except Exception as e:
        logger.error(f"Voice processing failed: {e}")
        await context.bot.send_message(
            chat_id=chat.id,
            text=f"⚠️ 语音处理失败: {e}"
        )
        return
    
    # --- 3. 存入历史（标记为 voice 类型）---
    reply_to_id = None
    reply_to_content = None
    
    if message.reply_to_message:
        reply_to_id = message.reply_to_message.message_id
        raw_ref_text = message.reply_to_message.text or "[Non-text message]"
        reply_to_content = (raw_ref_text[:30] + "..") if len(raw_ref_text) > 30 else raw_ref_text
    
    await history_service.add_message(
        chat.id,
        "user",
        transcribed_text,  # 存储转录的文字
        message_id=message.message_id,
        reply_to_id=reply_to_id,
        reply_to_content=reply_to_content,
        message_type="voice"  # 标记为语音消息
    )
    
    # --- 4. 触发回复 ---
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
        
        # --- 检查是否需要语音回复 ---
        last_message_type = await voice_service.get_last_user_message_type(chat_id)
        
        if last_message_type == "voice":
            # 用户最后一条消息是语音 → 尝试语音回复
            try:
                if await voice_service.is_tts_configured():
                    logger.info(f"TTS Mode: Generating voice reply...")
                    
                    # --- 1. 解析 LLM 输出 (提取文字 & 表情) ---
                    # 参考 SenderService 的解析逻辑
                    # 匹配标签: <chat attr="...">content</chat>
                    tag_pattern = r"<chat(?P<attrs>[^>]*)>(?P<content>.*?)</chat>"
                    match = re.search(tag_pattern, reply_content, flags=re.DOTALL)
                    
                    clean_text = reply_content
                    react_emoji = None
                    reply_to_msg_id = None
                    
                    if match:
                        attrs_raw = match.group("attrs")
                        clean_text = match.group("content").strip()
                        
                        # 提取 react 属性
                        react_match = re.search(r'react=["\']([^"\']+)["\']', attrs_raw)
                        if react_match:
                            react_emoji = react_match.group(1).strip()
                            # 简单清洗表情 (移除 ID 后缀)
                            if ":" in react_emoji:
                                react_emoji = react_emoji.split(":")[0].strip()
                                
                        # 提取 reply 属性
                        reply_match = re.search(r'reply=["\'](\d+)["\']', attrs_raw)
                        if reply_match:
                             try:
                                 reply_to_msg_id = int(reply_match.group(1))
                             except: pass

                    # 兜底：如果解析后为空，回退到原始文本 (防止空语音)
                    target_text_for_tts = clean_text if clean_text else reply_content
                    
                    # --- 2. 处理表情回应 ---
                    if react_emoji and react_emoji in sender_service.TG_FREE_REACTIONS:
                        try:
                            # 查找目标消息 ID (优先使用 XML 指定的回复 ID，否则找最后一条 user 消息)
                            target_react_id = reply_to_msg_id
                            
                            if not target_react_id and history_msgs:
                                last_user_msg = next((m for m in reversed(history_msgs) if m.role == 'user'), None)
                                if last_user_msg:
                                    target_react_id = last_user_msg.message_id
                            
                            if target_react_id:
                                from telegram import ReactionTypeEmoji
                                await context.bot.set_message_reaction(
                                    chat_id=chat_id,
                                    message_id=target_react_id,
                                    reaction=[ReactionTypeEmoji(react_emoji)]
                                )
                                logger.info(f"Voice Mode: Reacted {react_emoji} to MSG {target_react_id}")
                        except Exception as e:
                            logger.warning(f"Voice Mode: Failed to set reaction: {e}")

                    # --- 3. 模拟录制时长 ---
                    # 拟人化时长：每字符 0.15s，上限 5s
                    duration = min(len(target_text_for_tts) * 0.15, 5.0)
                    
                    # 发送 "正在录音..." 状态
                    await context.bot.send_chat_action(chat_id=chat_id, action=constants.ChatAction.RECORD_VOICE)
                    await asyncio.sleep(duration)

                    # --- 4. 生成 & 发送语音 ---
                    # 先存储助手回复 (存原始带标签文本，保持上下文一致性)
                    await history_service.add_message(
                        chat_id=chat_id,
                        role="assistant",
                        content=reply_content,
                        message_type="text"
                    )
                    
                    # 生成语音 (使用清洗后的纯文本)
                    voice_bytes = await voice_service.text_to_speech(target_text_for_tts)
                    
                    # 发送 "正在上传..." 状态
                    await context.bot.send_chat_action(chat_id=chat_id, action=constants.ChatAction.UPLOAD_VOICE)
                    # 稍微模拟上传耗时
                    await asyncio.sleep(0.5)

                    # 发送语音消息
                    from io import BytesIO
                    await context.bot.send_voice(
                        chat_id=chat_id,
                        voice=BytesIO(voice_bytes),
                        reply_to_message_id=reply_to_msg_id
                    )
                    
                    logger.info(f"Voice reply sent successfully")
                    return
                    
                else:
                    # TTS 未配置，降级为文字回复并提示
                    logger.warning("TTS not configured, fallback to text reply")
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text="⚠️ 语音回复功能未配置，请管理员使用 /dashboard 配置\n\n" + reply_content
                    )
                    await history_service.add_message(
                        chat_id=chat_id,
                        role="assistant",
                        content=reply_content
                    )
                    return
                    
            except TTSNotConfiguredError as e:
                logger.warning(f"TTS not configured: {e}")
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="⚠️ 语音回复功能未配置\n\n" + reply_content
                )
                await history_service.add_message(
                    chat_id=chat_id,
                    role="assistant",
                    content=reply_content
                )
                return
            except Exception as e:
                logger.error(f"TTS failed: {e}, fallback to text")
                # TTS 失败，降级为文字回复
                pass
        
        # 默认文字回复
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
