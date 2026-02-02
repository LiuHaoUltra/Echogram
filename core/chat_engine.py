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
    
    # --- 2. 准备上下文并调用多模态模型 ---
    try:
        voice = message.voice
        file = await context.bot.get_file(voice.file_id)
        voice_bytes = await file.download_as_bytearray()
        
        # 获取系统提示词 (复用 generate_response 逻辑)
        configs = await config_service.get_all_settings()
        system_prompt_custom = configs.get("system_prompt")
        timezone = configs.get("timezone", "UTC")
        dynamic_summary = await summary_service.get_summary(chat.id)
        
        system_prompt = prompt_builder.build_system_prompt(
            system_prompt_custom, 
            timezone=timezone, 
            dynamic_summary=dynamic_summary
        )
        
        # 获取历史记录(用于上下文)
        history_objs = await history_service.get_recent_messages(chat.id, limit=20)
        history_context = []
        for h in history_objs:
            role = h.role
            # 将历史中的 system 角色映射为 user 角色以便模型理解(可选，或保持 system)
            # 这里保持原样
            history_context.append({"role": role, "content": h.content})
            
        # 调用 Voice Service (多模态对话)
        # 返回: <transcript>...</transcript><chat>...</chat>
        xml_response = await voice_service.chat_with_voice(
            bytes(voice_bytes), 
            system_prompt, 
            history_context
        )
        
        if not xml_response:
             await context.bot.send_message(chat.id, "⚠️ AI 无法处理该语音，请重试")
             return

        # --- 3. 解析 XML ---
        # 提取 Transcript
        transcript = "[无法识别语音内容]"
        match_transcript = re.search(r"<transcript>(.*?)</transcript>", xml_response, flags=re.DOTALL)
        if match_transcript:
            transcript = match_transcript.group(1).strip()
            
        # 提取 Chat Reply (去除 transcript 标签后的剩余部分，或者直接提取 chat 标签)
        # 用正则提取所有 <chat> 标签的内容重新组合，或者直接把 xml_response 中的 <transcript> 移除
        # 更稳健的做法: 提取 transcript 后，把 transcript 标签从 xml_response 中移除，剩下的就是回复内容 (包含 <chat> 标签)
        # 但通常我们只需要提取 <chat> 里的内容 ? 
        # 用户之前的逻辑是: <chat> 标签是用来给 sender_service 解析表情的。
        # 所以我们需要保留 <chat> 标签给 sender_service。
        # 简单处理: 移除 <transcript>...</transcript> 块
        reply_content = re.sub(r"<transcript>.*?</transcript>", "", xml_response, flags=re.DOTALL).strip()
        
        # 如果回复为空 (只有 transcript?) -> 兜底
        if not reply_content:
             reply_content = "<chat>...</chat>"

        logger.info(f"Voice Processed: Transcript='{transcript[:30]}...', Reply='{reply_content[:30]}...'")

        # --- 4. 存入历史 ---
        # 4.1 用户消息 (Transcript)
        reply_to_id = None
        reply_to_content = None
        if message.reply_to_message:
            reply_to_id = message.reply_to_message.message_id
            raw_ref_text = message.reply_to_message.text or "[Non-text message]"
            reply_to_content = (raw_ref_text[:30] + "..") if len(raw_ref_text) > 30 else raw_ref_text

        await history_service.add_message(
            chat.id,
            "user",
            transcript,
            message_id=message.message_id,
            reply_to_id=reply_to_id,
            reply_to_content=reply_to_content,
            message_type="voice"
        )
        
        # 4.2 助手回复
        # 注意: 这里我们跳过了 LazySender 的队列，因为语音回复是即时的且已经生成好了
        # 直接存入历史并发送
        assistant_msg_id = await history_service.add_message(
            chat.id,
            "assistant",
            reply_content
        )
        
        # --- 5. 发送回复 (TTS Included) ---
        # 使用 SenderService 解析 <chat> 标签并发送
        # 注意: SenderService 需要处理 reply_content (包含 <chat> 标签)
        # 我们手动构造一个 SenderService 调用
        
        # 检查是否需要 TTS (根据配置)
        # SenderService 内部 logic: process_reply(chat_id, text, reply_to_msg_id)
        # 这里的 text 是 reply_content
        # 我们需要确保 reply_content 包含 <chat> 标签，SenderService 会解析它
        
        # 特殊逻辑: 强制 SenderService 认为这是 Voice 回复? 
        # SenderService 会检查 <chat> 标签。如果包含，它会处理 react。
        # 但是 TTS? SenderService 目前没有集成 TTS 逻辑 (TTS 逻辑在 generate_response 里)
        # 这是一个问题。generate_response 里有 TTS 逻辑。SenderService 没有。
        # 我需要把 generate_response 里的 TTS 逻辑搬过来，或者调用一个公共方法。
        
        # Plan B: 复用 SenderService ? 
        # SenderService 主要负责发送文本和 React。
        # TTS 逻辑目前是在 generate_response 里写的 (Step 429 lines 280+).
        # 我应该把那段逻辑提取出来，或者在这里复制一份。
        # 为了快速修复，直接在这里实现发送逻辑 (包含 TTS)。
        
        # 解析回复
        tag_pattern = r"<chat(?P<attrs>[^>]*)>(?P<content>.*?)</chat>"
        match = re.search(tag_pattern, reply_content, flags=re.DOTALL)
        
        text_to_send = reply_content
        react_emoji = None
        
        if match:
            attrs_raw = match.group("attrs")
            text_to_send = match.group("content").strip()
            # 提取 react
            react_match = re.search(r'react=["\']([^"\']+)["\']', attrs_raw)
            if react_match:
                react_emoji = react_match.group(1).split(":")[0].strip()
        else:
             # 如果没有 chat 标签，直接作为文本
             text_to_send = reply_content.replace("<chat>", "").replace("</chat>", "").strip()

        # 发送 Reaction
        if react_emoji:
            try:
                await context.bot.set_message_reaction(
                    chat_id=chat.id,
                    message_id=message.message_id,
                    reaction=react_emoji
                )
            except Exception as e:
                logger.warning(f"Reaction failed: {e}")

        # 发送 TTS 或 文本
        if await voice_service.is_tts_configured():
             try:
                 voice_audio = await voice_service.text_to_speech(text_to_send)
                 await context.bot.send_voice(
                     chat_id=chat.id,
                     voice=voice_audio,
                     caption=None # 纯语音不带文字? 或者带? 
                     # 用户偏好: 纯语音或者语音+文字? 
                     # 通常语音回复不带文字，或者文字作为 caption (如果短)。
                     # 这里暂不发文字，只发语音，正如 generate_response 里的逻辑。
                 )
             except Exception as e:
                 logger.error(f"TTS Send Failed: {e}")
                 # Fallback to text
                 await context.bot.send_message(chat_id=chat.id, text=text_to_send)
        else:
             # 仅文本回复
             await context.bot.send_message(chat_id=chat.id, text=text_to_send)

    except Exception as e:
        logger.error(f"Voice processing failed: {e}")
        await context.bot.send_message(chat.id, f"⚠️ 处理失败: {e}")

    # 触发总结 (Async)
    try:
        asyncio.create_task(summary_service.check_and_summarize(chat.id))
    except Exception:
        pass

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
                pass # Continue to processing
            except Exception:
                pass

        # --- 5. 处理回复 (Multi-Bubble) ---
        # 提取所有 <chat> 标签 (Tag Block) 包括属性和内容
        # 正则说明: 匹配 (<chat[^>]*>.*?</chat>)，提取完整块
        chat_blocks = re.findall(r"(<chat[^>]*>.*?</chat>)", reply_content, flags=re.DOTALL)
        
        # 如果没匹配到，构造一个虚拟块
        if not chat_blocks:
            chat_blocks = [f"<chat>{reply_content.strip()}</chat>"]

        # 先存储助手历史 (One Entry, Full Context)
        # 保持数据库整洁，存储完整的 XML 回复
        await history_service.add_message(
            chat_id=chat.id,
            role="assistant",
            content=reply_content,
            message_type="text"
        )

        # 逐个气泡处理
        for index, block in enumerate(chat_blocks):
            # 解析 Attributes
            attrs_match = re.search(r"<chat([^>]*)>", block)
            attrs_raw = attrs_match.group(1) if attrs_match else ""
            
            # 解析 Content
            content_match = re.search(r">(.+?)</chat>", block, flags=re.DOTALL)
            text_part = content_match.group(1).strip() if content_match else ""
            
            # 1. 提取 React
            react_emoji = None
            react_match = re.search(r'react=["\']([^"\']+)["\']', attrs_raw)
            if react_match:
                react_emoji = react_match.group(1).split(":")[0].strip()
            
            # 2. 提取 Reply ID
            reply_to_target = None
            reply_match = re.search(r'reply=["\'](\d+)["\']', attrs_raw)
            if reply_match:
                 try: 
                     reply_to_target = int(reply_match.group(1)) 
                 except: pass

            # --- 发送 Reaction ---
            if react_emoji and react_emoji in sender_service.TG_FREE_REACTIONS:
                try:
                    target_id = reply_to_target if reply_to_target else message.message_id
                    from telegram import ReactionTypeEmoji
                    await context.bot.set_message_reaction(
                        chat_id=chat.id,
                        message_id=target_id,
                        reaction=[ReactionTypeEmoji(react_emoji)]
                    )
                except Exception as e:
                    logger.warning(f"Voice Mode: Failed to process reaction {react_emoji}: {e}")

            # --- 发送 Voice Bubble ---
            # 清洗文本 (移除所有 XML 标签，防止 TTS 读出标签)
            text_part = re.sub(r'<[^>]+>', '', text_part).strip()

            if text_part:
                # 拟人化时长
                duration = min(len(text_part) * 0.2, 5.0)
                await context.bot.send_chat_action(chat_id=chat.id, action=constants.ChatAction.RECORD_VOICE)
                await asyncio.sleep(duration)
                
                if await voice_service.is_tts_configured():
                    try:
                        voice_bytes = await voice_service.text_to_speech(text_part)
                        
                        await context.bot.send_chat_action(chat_id=chat.id, action=constants.ChatAction.UPLOAD_VOICE)
                        
                        # 显式指定 filename 解决 0:00 bug
                        # 使用时间戳防重名 (或简单 voice.ogg)
                        import time
                        await context.bot.send_voice(
                            chat_id=chat.id,
                            voice=voice_bytes,
                            filename=f"voice_{int(time.time())}_{index}.ogg", 
                            caption=None
                        )
                    except Exception as e:
                        logger.error(f"TTS Bubble Failed: {e}")
                        await context.bot.send_message(chat_id=chat.id, text=text_part)
                else:
                    # Fallback to text
                    await context.bot.send_message(chat_id=chat.id, text=text_part)
                
                # 稍微间隔，避免消息乱序
                await asyncio.sleep(0.3)
                    

        
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
