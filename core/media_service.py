"""媒体服务模块 - TTS/ASR/Image 处理功能"""

import base64
import aiohttp
import asyncio
from openai import AsyncOpenAI

from core.config_service import config_service
from utils.logger import logger
from utils.prompts import prompt_builder
from utils.config_validator import safe_float_config


class MediaServiceError(Exception):
    """媒体服务基础异常"""
    pass


class TTSNotConfiguredError(MediaServiceError):
    """TTS 未启用或未配置"""
    pass


class MediaService:
    """媒体与多模态服务：ASR、TTS 及 Vision"""
    
    # is_asr_configured 已移除 (统一使用主模型)
    
    async def process_image_to_base64(self, file_bytes: bytes) -> str:
        """
        将图片字节流转换为 Base64 字符串 (异步包装)
        """
        return await asyncio.to_thread(self._sync_process_image_to_base64, file_bytes)

    def _sync_process_image_to_base64(self, file_bytes: bytes) -> str:
        """
        [Sync] CPU 密集型图片处理
        """
        import io
        from PIL import Image
        
        try:
            image = Image.open(io.BytesIO(file_bytes))
            
            # 尺寸限制 (OpenRouter/OpenAI 最佳实践)
            w, h = image.size
            
            # 计算缩放比例
            ratio = 1.0
            if max(w, h) > 2048:
                ratio = min(ratio, 2048 / max(w, h))
            if min(w, h) * ratio > 768:
                ratio = min(ratio, 768 / min(w, h))
                
            if ratio < 1.0:
                new_size = (int(w * ratio), int(h * ratio))
                image = image.resize(new_size, Image.Resampling.LANCZOS)
            
            # 统一转为 RGB
            if image.mode in ('RGBA', 'P'):
                image = image.convert('RGB')
                
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=85)
            return base64.b64encode(buffer.getvalue()).decode('utf-8')
            
        except Exception as e:
            logger.error(f"Image processing failed: {e}")
            return base64.b64encode(file_bytes).decode('utf-8')


    async def process_audio_to_base64(self, file_bytes: bytes) -> str:
        """
        将 OGG 语音转换为 WAV Base64 (异步包装)
        """
        return await asyncio.to_thread(self._sync_process_audio_conversion, file_bytes)

    def _sync_process_audio_conversion(self, file_bytes: bytes) -> str:
        """
        [Sync] 音频转码 (OGG -> WAV)
        """
        import uuid
        import os
        from pydub import AudioSegment
        
        temp_ogg_path = f"/tmp/{uuid.uuid4()}.ogg"
        temp_wav_path = f"/tmp/{uuid.uuid4()}.wav"
        
        try:
            with open(temp_ogg_path, "wb") as f:
                f.write(file_bytes)
            
            audio = AudioSegment.from_ogg(temp_ogg_path)
            audio.export(temp_wav_path, format="wav")
            
            with open(temp_wav_path, "rb") as f:
                wav_bytes = f.read()
                return base64.b64encode(wav_bytes).decode('utf-8')
                
        except Exception as e:
            logger.error(f"Audio conversion failed: {e}")
            return None
        finally:
            try:
                if os.path.exists(temp_ogg_path): os.remove(temp_ogg_path)
                if os.path.exists(temp_wav_path): os.remove(temp_wav_path)
            except Exception as e:
                logger.warning(f"Failed to clean temp files: {e}")


    async def is_tts_configured(self) -> bool:
        """检查 TTS 是否已配置"""
        tts_enabled = await config_service.get_value("tts_enabled", "false")
        tts_url = await config_service.get_value("tts_api_url")
        tts_ref_audio = await config_service.get_value("tts_ref_audio_path")
        
        # 安全的字符串比较
        is_enabled = str(tts_enabled).strip().lower() in ("true", "1", "yes")
        
        if not is_enabled or not tts_url or not tts_ref_audio:
            logger.warning(f"TTS Config Check Failed: Enabled={is_enabled} ({tts_enabled}), URL={bool(tts_url)}, RefAudio={bool(tts_ref_audio)}")
        
        return is_enabled and bool(tts_url) and bool(tts_ref_audio)
    
    async def chat_with_voice(self, voice_file_bytes: bytes, system_prompt: str, history_messages: list, chat_id: int) -> str:
        """
        语音多模态对话 (Multimodal Audio-to-Text)
        
        Args:
            voice_file_bytes: 原始语音文件 (OGG)
            system_prompt: 当前人格设定的 System Prompt
            history_messages: 历史对话上下文 (OpenAI 格式列表)
            chat_id: 当前会话 ID
            
        Returns:
            str: 原始 LLM 响应，包含 <transcript> 和 <chat> 标签
        """
        api_key = await config_service.get_value("api_key")
        base_url = await config_service.get_value("api_base_url")
        model_name = await config_service.get_value("model_name") 
        if not model_name:
             # Fallback if not set (unlikely)
             model_name = "gpt-4o-audio-preview"
        
        if not api_key:
            raise MediaServiceError("API Key 未配置")
        
        # Base64 编码音频 (OGG -> WAV 转换) - Offload to thread
        try:
            base64_audio = await asyncio.to_thread(self._sync_process_audio_conversion, voice_file_bytes)
            if not base64_audio:
                 raise MediaServiceError("音频预处理返回为空")
        except Exception as e:
             logger.error(f"音频格式转换失败: {e}")
             raise MediaServiceError(f"音频预处理失败: {e}")
        
        # --- 构造多模态 Messages ---
        
        # 1. 构建 System Prompt (注入语音模式协议)
        # 修正：现在 system_prompt 参数传入的是原始人设，这里需要亲自获取摘要
        from core.summary_service import summary_service
        dynamic_summary = await summary_service.get_summary(chat_id)
        # 更稳健做法：在 chat_with_voice 外部组装，但这样又会回到嵌套。
        # 决定：由 chat_engine 仅传 Soul，这里负责组装核心协议。
        
        # 重新获取配置以保证新鲜度
        timezone = await config_service.get_value("timezone", "UTC")
        # 注意：为了避免循环引用，summary_service 已通过 local import
        
        final_system_prompt = prompt_builder.build_system_prompt(
            soul_prompt=system_prompt, # 这里就是原始人设
            timezone=timezone,
            dynamic_summary=dynamic_summary,
            has_voice=True # Voice interaction always assumes voice present
        )
        
        # 2. 构建上下文
        messages = []
        messages.append({"role": "system", "content": final_system_prompt})
        
        # 插入历史记录 (仅最近几条，并进行格式化处理)
        if history_messages:
            import pytz
            try:
                tz = pytz.timezone(timezone)
            except:
                tz = pytz.UTC

            formatted_history = []
            for h_obj in history_messages[-10:]:
                # 假设 h_obj 是从 history_service.get_recent_messages 传来的字典
                # 包含: role, content, message_id, timestamp (可能由于 DB 查询没带，需确认)
                # 查阅 chat_engine.py 的 process_voice_message_entry，传进来的是带 message_id 的字典
                
                # 获取时间字符串 (如果字典里没有 timestamp，显示 Unknown)
                ts = h_obj.get('timestamp')
                if ts:
                    try:
                        if ts.tzinfo is None: ts = ts.replace(tzinfo=pytz.UTC)
                        time_str = ts.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S")
                    except:
                        time_str = "Time Error"
                else:
                    time_str = "Unknown Time"
                
                msg_id = h_obj.get('message_id')
                msg_id_str = f"MSG {msg_id}" if msg_id else "MSG ?"
                msg_type_str = h_obj.get('message_type', 'Text').capitalize()
                prefix = f"[{msg_id_str}] [{time_str}] [{msg_type_str}] "
                
                formatted_history.append({
                    "role": h_obj["role"],
                    "content": prefix + h_obj["content"]
                })
            messages.extend(formatted_history)
            
        # 3. 当前语音消息
        logger.info(f"Preparing Audio Payload: WAV Size={len(base64.b64decode(base64_audio))} bytes")
        
        user_content = [
            {
                "type": "text",
                "text": "Please process this audio message according to the Voice Mode Protocol."
            },
            {
                "type": "input_audio",
                "input_audio": {
                    "data": base64_audio,
                    "format": "wav"
                }
            }
        ]
        messages.append({"role": "user", "content": user_content})

        try:
            logger.info(f"VoiceChat: 调用模型 {model_name} (Multimodal)...")
            logger.debug(f"Payload Preview: {str(messages)[:500]}...") # Log payload start
            
            client = AsyncOpenAI(api_key=api_key, base_url=base_url)
            # 400 Bad Request Fix: gpt-audio-mini 依然需要 modalities=["text"] 吗？
            # 官方文档显示 Audio Output 暂未完全开放 API (即 modalities=["audio", "text"])，
            # 这里我们只请求文字回复，所以保持 modalities=["text"] 是一安全的，甚至可能是必须的。
            # 获取 Temperature
            temp_val = await config_service.get_value("temperature", "0.7")
            temperature = safe_float_config(temp_val, 0.7, 0.0, 2.0)

            response = await client.chat.completions.create(
                model=model_name,
                messages=messages,
                temperature=temperature,
                max_tokens=4000,
                modalities=["text"]
            )
            
            if not response.choices or not response.choices[0].message.content:
                logger.warning(f"VoiceChat 返回空内容")
                return ""
            
            raw_output = response.choices[0].message.content.strip()
            logger.info(f"VoiceChat 成功: {raw_output[:100]}...")
            
            return raw_output
            
        except Exception as e:
            logger.error(f"VoiceChat 调用失败: {e}")
            raise MediaServiceError(f"语音服务不可用: {e}")
    
    async def text_to_speech(self, text: str) -> bytes:
        """
        TTS：文字转语音（使用 GPT-SoVITS API）
        
        Args:
            text: 待合成的文字
            
        Returns:
            语音文件字节流（OGG 格式）
            
        Raises:
            TTSNotConfiguredError: TTS 未配置
            MediaServiceError: 语音合成失败
        """
        # 输入验证
        if not text or not text.strip():
            raise MediaServiceError("待合成文本为空")
        
        # 检查配置
        tts_enabled = await config_service.get_value("tts_enabled", "false")
        is_enabled = str(tts_enabled).strip().lower() in ("true", "1", "yes")
        if not is_enabled:
            raise TTSNotConfiguredError("TTS 功能未启用")
        
        tts_url = await config_service.get_value("tts_api_url")
        tts_ref_audio = await config_service.get_value("tts_ref_audio_path")
        
        if not tts_url or not tts_ref_audio:
            raise TTSNotConfiguredError("TTS API URL 或参考音频路径未配置")
        
        # 安全转换 speed_factor
        try:
            speed_factor_str = await config_service.get_value("tts_speed_factor", "1.0")
            speed_factor = float(speed_factor_str)
            # 限制范围 0.5-2.0
            speed_factor = max(0.5, min(2.0, speed_factor))
        except (ValueError, TypeError):
            logger.warning(f"无效的 speed_factor 配置，使用默认值 1.0")
            speed_factor = 1.0
        
        # 准备请求参数
        payload = {
            "text": text.strip(),
            "text_lang": await config_service.get_value("tts_text_lang", "zh"),
            "ref_audio_path": tts_ref_audio,
            "prompt_lang": await config_service.get_value("tts_prompt_lang", "zh"),
            "prompt_text": await config_service.get_value("tts_ref_text", ""),
            "media_type": "wav",  # 使用 WAV 作为中间格式，便于后续标准 OPUS 转换
            "speed_factor": speed_factor
        }
        
        try:
            logger.info(f"TTS: 合成语音 ({len(text)} 字符)...")
            
            # 直接使用配置的 URL (遵循用户输入)
            api_endpoint = tts_url

            # 发起请求获取原始音频
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    api_endpoint,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=45) # 稍微增加超时
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        raise MediaServiceError(f"TTS API 返回错误: {response.status} - {error_text}")
                    
                    raw_audio_bytes = await response.read()
                    
                    # 2. 使用 ffmpeg (通过 pydub) 转换为标准 Telegram 语音格式 (OGG/OPUS)
                    logger.info("TTS: 正在进行标准 OGG/OPUS 编码转换...")
                    try:
                        # Offload CPU-heavy transcoding
                        standard_audio_bytes = await asyncio.to_thread(self._sync_convert_wav_to_ogg, raw_audio_bytes)
                        
                        logger.info(f"TTS 成功: 生成并转换 {len(standard_audio_bytes)} 字节标准语音文件")
                        return standard_audio_bytes
                        
                    except Exception as e:
                        logger.error(f"TTS 音频格式强制转换失败: {e}")
                        raise MediaServiceError(f"语音格式转换失败 (请确认环境已安装 ffmpeg): {e}")

        except TTSNotConfiguredError:
            # 重新抛出配置错误
            raise
        except aiohttp.ClientError as e:
            logger.error(f"TTS 网络请求失败: {e}")
            raise MediaServiceError(f"TTS 网络请求失败: {e}")
        except MediaServiceError:
            # 重新抛出已知的语音服务错误
            raise
        except Exception as e:
            logger.error(f"TTS 合成失败（未预期错误）: {e}")
            raise MediaServiceError(f"TTS 合成失败: {e}")

    def _sync_convert_wav_to_ogg(self, wav_bytes: bytes) -> bytes:
        """
        [Sync] 音频转码 (WAV -> OGG/OPUS)
        """
        from pydub import AudioSegment
        import io
        
        # 加载原始音频 (WAV)
        audio = AudioSegment.from_file(io.BytesIO(wav_bytes), format="wav")
        # 导出为 OGG
        buffer = io.BytesIO()
        audio.export(buffer, format="ogg", codec="libopus")
        return buffer.getvalue()
    
    async def get_last_user_message_type(self, chat_id: int) -> str:
        """
        获取最后一条用户消息的类型
        
        Args:
            chat_id: 聊天 ID
            
        Returns:
            消息类型: 'text' 或 'voice'
        """
        from sqlalchemy import select
        from config.database import get_db_session
        from models.history import History
        
        async for session in get_db_session():
            stmt = select(History.message_type) \
                .where(History.chat_id == chat_id, History.role == "user") \
                .order_by(History.timestamp.desc()) \
                .limit(1)
            
            result = await session.execute(stmt)
            message_type = result.scalar_one_or_none()
            
            return message_type or "text"


    async def transcribe_audio(self, file_bytes: bytes) -> str:
        """
        [Shift-Left] 语音转文字 (使用配置的 media_model, e.g. gemini-2.0-flash)
        """
        import base64
        
        configs = await config_service.get_all_settings()
        api_key = configs.get("api_key")
        base_url = configs.get("api_base_url")
        
        # 直接使用配置的媒体模型
        model_name = configs.get("media_model")
        if not model_name: 
            return "[语音未处理: 未配置媒体模型]"
        
        if not api_key:
            return "[语音转录失败: 未配置 API Key]"

        try:
            # 转换为 Base64 (Gemini 等模型接收 Inline Data)
            # 注意: 这里假设模型支持音频 (如 gemini-2.0-flash-exp)
            # 需要先转码为 wav base64 (复用 process_audio_to_base64)
            base64_audio = await self.process_audio_to_base64(file_bytes)
            if not base64_audio:
                return "[语音预处理失败]"

            client = AsyncOpenAI(api_key=api_key, base_url=base_url)
            
            response = await client.chat.completions.create(
                model=model_name,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Please transcribe this audio content verbatim. Only output the transcription text."},
                            {
                                "type": "input_audio",
                                "input_audio": {
                                    "data": base64_audio,
                                    "format": "wav"
                                }
                            }
                        ]
                    }
                ],
                max_tokens=500  # 转录通常不需要太长
            )
            
            if response.choices and response.choices[0].message.content:
                transcript = response.choices[0].message.content.strip()
                logger.info(f"Audio Transcribed ({model_name}): {transcript[:50]}...")
                return transcript
            
            return "[语音转录无结果]"
            
        except Exception as e:
            logger.error(f"Transcription failed: {e}")
            return f"[语音转录失败: {str(e)[:50]}]"

    async def caption_image(self, file_bytes: bytes) -> str:
        """
        [Shift-Left] 图片转文字描述 (利用配置的 media_model)
        """
        configs = await config_service.get_all_settings()
        api_key = configs.get("api_key")
        base_url = configs.get("api_base_url")
        
        # 优先使用 media_model, 降级到 gpt-4o-mini
        model_name = configs.get("media_model") or configs.get("model_name") or "gpt-4o-mini"
        
        if not api_key:
            return "[图片分析失败: 未配置 API Key]"

        try:
            base64_image = await self.process_image_to_base64(file_bytes)
            
            client = AsyncOpenAI(api_key=api_key, base_url=base_url)
            
            response = await client.chat.completions.create(
                model=model_name,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "请详细描述这张图片的内容，包括主要物体、文字、场景和氛围。直接输出描述，不要加前缀。"},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{base64_image}",
                                    "detail": "low" # 平衡成本与速度
                                }
                            }
                        ]
                    }
                ],
                max_tokens=300
            )
            
            if response.choices and response.choices[0].message.content:
                caption = response.choices[0].message.content.strip()
                logger.info(f"Image Captioned ({model_name}): {caption[:50]}...")
                return caption
            
            return "[图片分析无结果]"
            
        except Exception as e:
            logger.error(f"Image captioning failed: {e}")
            return f"[图片分析失败: {str(e)[:50]}]"

media_service = MediaService()
