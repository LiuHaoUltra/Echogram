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
from core.media_service import media_service, TTSNotConfiguredError, MediaServiceError
from utils.logger import logger
from utils.prompts import prompt_builder
from utils.config_validator import safe_int_config, safe_float_config
from core.sender_service import sender_service


async def process_message_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    HTTP/Telegram 消息入口 (文本)
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


async def process_photo_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    图片消息入口 (聚合模式)
    """
    user = update.effective_user
    chat = update.effective_chat
    message = update.message
    
    if not user or not chat or not message or not message.photo:
        return
        
    # --- 1. 访问控制 ---
    is_adm = is_admin(user.id)
    if chat.type == constants.ChatType.PRIVATE:
        if is_adm: pass
        return
    else:
        if not await access_service.is_whitelisted(chat.id):
            return
            
    logger.info(f"PHOTO [{chat.id}] from {user.first_name}")
    
    # 获取最大尺寸图片
    photo = message.photo[-1]
    file_id = photo.file_id
    
    # 存入历史 (占位)
    reply_to_id = None
    reply_to_content = None
    if message.reply_to_message:
        reply_to_id = message.reply_to_message.message_id
        raw_text = message.reply_to_message.text or "[Non-text message]"
        reply_to_content = (raw_text[:30] + "..") if len(raw_text) > 30 else raw_text

    await history_service.add_message(
        chat.id, "user", "[Image: Processing...]", 
        message_id=message.message_id,
        reply_to_id=reply_to_id, reply_to_content=reply_to_content,
        message_type="image", file_id=file_id
    )
    
    # 触发聚合
    await lazy_sender.on_message(chat.id, context)
    try:
        asyncio.create_task(summary_service.check_and_summarize(chat.id))
    except:
        pass


async def process_voice_message_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    语音消息入口 (聚合模式)
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
        if is_adm: pass
        return
    else:
        if not await access_service.is_whitelisted(chat.id):
            return
    
    logger.info(f"VOICE [{chat.id}] from {user.first_name}: {message.voice.duration}s")
    
    file_id = message.voice.file_id
    
    # 存入历史 (占位)
    reply_to_id = None
    reply_to_content = None
    if message.reply_to_message:
        reply_to_id = message.reply_to_message.message_id
        raw_text = message.reply_to_message.text or "[Non-text message]"
        reply_to_content = (raw_text[:30] + "..") if len(raw_text) > 30 else raw_text

    await history_service.add_message(
        chat.id, "user", "[Voice: Processing...]",
        message_id=message.message_id,
        reply_to_id=reply_to_id, reply_to_content=reply_to_content,
        message_type="voice", file_id=file_id
    )
    
    # 触发聚合
    await lazy_sender.on_message(chat.id, context)
    try:
        asyncio.create_task(summary_service.check_and_summarize(chat.id))
    except:
        pass


async def generate_response(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """
    核心回复生成逻辑 (支持多模态聚合)
    1. 获取历史
    2. 扫描 Recent Assistant 之后的 User Messages
    3. 提取 pending 的图片/语音并下载转换
    4. 构造 Multimodal Payload
    5. 调用 LLM
    6. 解析结果 (Summary/Transcript) 并回填 DB
    7. 发送回复
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
    # Token limit check
    target_tokens = safe_int_config(
        configs.get("history_tokens"),
        settings.HISTORY_WINDOW_TOKENS,
        min_val=100, max_val=50000
    )
    
    # 1. 获取基础历史记录
    history_msgs = await history_service.get_token_controlled_context(chat_id, target_tokens=target_tokens)
    
    # 2. 识别“尾部”聚合区间 (自上一条 Assistant 消息之后的所有 User 消息)
    last_assistant_idx = -1
    for i in range(len(history_msgs) - 1, -1, -1):
        if history_msgs[i].role == 'assistant':
            last_assistant_idx = i
            break
            
    if last_assistant_idx == -1:
        # 如果最近200条里都没有 assistant，全量作为 user 输入
        tail_msgs = history_msgs
        base_msgs = []
    else:
        base_msgs = history_msgs[:last_assistant_idx+1]
        tail_msgs = history_msgs[last_assistant_idx+1:]
        
    # 3. 扫描聚合区间内的 Pending 多模态内容 (Chronological Processing)
    pending_images_map = {} # msg_id -> msg object for backfill
    pending_voices_map = {} # msg_id -> msg object for backfill
    has_multimodal = False
    
    # 构建 Multimodal Content Blocks (严格按时间顺序)
    multimodal_content = []
    
    # 只要检测到由 lazy_sender 触发的聚合 (context.args check is not reliable here, rely on tail_msgs scanning)
    # 我们遍历 tail_msgs，只要发现任何 pending 媒体，就开启多模态模式
    
    # 预扫描以决定 system prompt mode
    for msg in tail_msgs:
        if "[Image: Processing...]" in msg.content or "[Voice: Processing...]" in msg.content:
            has_multimodal = True
            break

    if has_multimodal:
        logger.info(f"Multimodal Batch Triggered. Processing {len(tail_msgs)} tail messages chronologically.")
        
        for msg in tail_msgs:
            if msg.role != 'user': 
                continue
                
            content_marker = msg.content
            
            # Case A: Image
            if msg.message_type == 'image' and msg.file_id and "[Image: Processing...]" in content_marker:
                try:
                    # Download & Process
                    f = await context.bot.get_file(msg.file_id)
                    b = await f.download_as_bytearray()
                    b64 = await media_service.process_image_to_base64(bytes(b))
                    
                    if b64:
                        # Append Interleaved Blocks
                        multimodal_content.append({
                            "type": "text",
                            "text": f"Image [MSG {msg.message_id}]:"
                        })
                        multimodal_content.append({
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{b64}"
                            }
                        })
                        pending_images_map[msg.message_id] = msg
                except Exception as e:
                    logger.error(f"Image DL failed for MSG {msg.message_id}: {e}")
                    multimodal_content.append({"type": "text", "text": f"[Image Download Failed for MSG {msg.message_id}]"})

            # Case B: Voice
            elif msg.message_type == 'voice' and msg.file_id and "[Voice: Processing...]" in content_marker:
                try:
                    # Download & Process
                    f = await context.bot.get_file(msg.file_id)
                    b = await f.download_as_bytearray()
                    b64 = await media_service.process_audio_to_base64(bytes(b))
                    
                    if b64:
                        # Append Interleaved Blocks
                        multimodal_content.append({
                            "type": "text",
                            "text": f"Voice [MSG {msg.message_id}]:"
                        })
                        multimodal_content.append({
                            "type": "input_audio",
                            "input_audio": {
                                "data": b64,
                                "format": "wav"
                            }
                        })
                        pending_voices_map[msg.message_id] = msg
                except Exception as e:
                    logger.error(f"Voice DL failed for MSG {msg.message_id}: {e}")
                    multimodal_content.append({"type": "text", "text": f"[Voice Download Failed for MSG {msg.message_id}]"})
            
            # Case C: Text
            else:
                 # 纯文本消息 (可能是之前的文本，或者是混合在中间的文本)
                 # 过滤掉非 pending 的占位符? 不，只有 "[Image: Processing...]" 是我们要替换的。
                 # 如果用户发了文字 "Look at this"，这里直接作为 text block
                 text_content = msg.content
                 if text_content:
                     multimodal_content.append({
                         "type": "text",
                         "text": text_content
                     })

        # Final Payload Assignment
        # append a final instructions prompt if needed? 
        # The system prompt handles the protocol.
        # Maybe add a small footer to ensure LLM knows context ended?
        if not multimodal_content:
             multimodal_content.append({"type": "text", "text": "Please process the updated context."})
             
        # Add "Please process..." trigger if the last item wasn't an explicit instruction?
        # Better to just let the system prompt drive it.
        
        messages.append({"role": "user", "content": multimodal_content})


        # 普通文本模式：直接追加
        for h in tail_msgs:
            # 同样格式化
            time_str = "Unknown"
            if h.timestamp:
                 try:
                    dt = h.timestamp.replace(tzinfo=pytz.UTC) if h.timestamp.tzinfo is None else h.timestamp
                    time_str = dt.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S")
                 except: pass
            msg_id_str = f"MSG {h.message_id}" if h.message_id else "MSG ?"
            prefix = f"[{msg_id_str}] [{time_str}] "
            if h.reply_to_content:
                prefix += f'(Reply to "{h.reply_to_content}") '
            messages.append({"role": h.role, "content": prefix + h.content})

    # 7. 调用 LLM
    current_temp = safe_float_config(configs.get("temperature", "0.7"), 0.7, 0.0, 2.0)
    
    try:
        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        # 注意: modalities=["text"] 在 audio preview 模型中通常是必须的
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=current_temp,
            max_tokens=4000,
            modalities=["text"] 
        )
        
        if not response.choices or not response.choices[0].message.content:
            await context.bot.send_message(chat_id, "⚠️ AI 返回空内容")
            return
            
        reply_content = response.choices[0].message.content.strip()
        logger.info(f"LLM Response: {reply_content[:100]}...")
        
        # 8. 解析结果并回填 (Backfill)
        # 8.1 语音 Transcript (XML + MsgID 匹配)
        transcript_matches = list(re.finditer(r'<transcript\s+msg_id=["\'](\d+)["\']>(.*?)</transcript>', reply_content, flags=re.DOTALL))
        
        if transcript_matches and pending_voices_map:
            for match in transcript_matches:
                try:
                    msg_id = int(match.group(1))
                    content = match.group(2).strip()
                    
                    if msg_id in pending_voices_map:
                        target_msg = pending_voices_map[msg_id]
                        await history_service.update_message_content_by_file_id(target_msg.file_id, content)
                        logger.info(f"Backfilled transcript for MSG {msg_id}")
                    else:
                        logger.warning(f"Transcript msg_id {msg_id} not found in pending voices map")
                except Exception as e:
                     logger.error(f"Failed to parse transcript match: {e}")

            # 清理回复中的 transcript 标签，避免发给用户
            reply_content = re.sub(r"<transcript.*?>.*?</transcript>", "", reply_content, flags=re.DOTALL).strip()
            
        # 8.2 图片 Summary (XML + MsgID 匹配)
        # 匹配 <img_summary msg_id="123">...</img_summary>
        img_summary_matches = list(re.finditer(r'<img_summary\s+msg_id=["\'](\d+)["\']>(.*?)</img_summary>', reply_content, flags=re.DOTALL))
        
        if img_summary_matches and pending_images_map:
            for match in img_summary_matches:
                try:
                    msg_id = int(match.group(1))
                    content = match.group(2).strip()
                    
                    if msg_id in pending_images_map:
                        target_msg = pending_images_map[msg_id]
                        final_summary = f"[Image Summary: {content}]" 
                        await history_service.update_message_content_by_file_id(target_msg.file_id, final_summary)
                        logger.info(f"Backfilled summary for MSG {msg_id}")
                    else:
                        logger.warning(f"Image summary msg_id {msg_id} not found in pending images map")
                except Exception as e:
                    logger.error(f"Failed to parse image summary match: {e}")
            
            # 清理回复中的 img_summary 标签
            reply_content = re.sub(r"<img_summary.*?>.*?</img_summary>", "", reply_content, flags=re.DOTALL).strip()
            
        if not reply_content:
            reply_content = "<chat>...</chat>" # 兜底

        # 9. 发送回复
        await sender_service.send_llm_reply(
            chat_id=chat_id,
            reply_content=reply_content,
            context=context,
            # 这里 history_msgs 还是旧的 (含 placeholder)。
            # 理想情况下应该更新 history_msgs 里的内容再传给 sender?
            # 但 sender 主要用它来做表情回应定位 (message_id)，内容不关键。
            history_msgs=history_msgs 
        )

    except Exception as e:
        logger.error(f"API Call failed: {e}")
        if is_admin(chat_id):
            await context.bot.send_message(chat_id, f"❌ API Error: {e}")


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
