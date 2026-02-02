"""语音服务模块 - TTS/ASR 功能"""

import base64
import aiohttp
from openai import AsyncOpenAI

from core.config_service import config_service
from utils.logger import logger


class VoiceServiceError(Exception):
    """语音服务基础异常"""
    pass


class ASRNotConfiguredError(VoiceServiceError):
    """ASR 模型未配置"""
    pass


class TTSNotConfiguredError(VoiceServiceError):
    """TTS 未启用或未配置"""
    pass


class VoiceService:
    """语音服务：ASR（语音转文字）和 TTS（文字转语音）"""
    
    async def is_asr_configured(self) -> bool:
        """检查 ASR 是否已配置"""
        asr_model = await config_service.get_value("asr_model_name")
        return bool(asr_model)
    
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
    
    async def speech_to_text(self, voice_file_bytes: bytes) -> str:
        """
        ASR：语音转文字（使用 OpenAI 兼容的多模态接口）
        
        Args:
            voice_file_bytes: 语音文件字节流
            
        Returns:
            识别的文字内容
            
        Raises:
            ASRNotConfiguredError: ASR 模型未配置
            VoiceServiceError: 语音识别失败
        """
        # 输入验证
        if not voice_file_bytes or len(voice_file_bytes) == 0:
            raise VoiceServiceError("语音文件为空")
        
        # 检查配置
        asr_model = await config_service.get_value("asr_model_name")
        if not asr_model:
            raise ASRNotConfiguredError("ASR 模型未配置")
        
        api_key = await config_service.get_value("api_key")
        base_url = await config_service.get_value("api_base_url")
        
        if not api_key:
            raise ASRNotConfiguredError("API Key 未配置")
        
        # Base64 编码音频 (OGG -> FLAC 转换)
        # OpenAI Chat API 不支持 OGG，需转换为 FLAC (更严格兼容)
        import uuid
        import os
        from pydub import AudioSegment
        import io
        
        temp_ogg_path = f"/tmp/{uuid.uuid4()}.ogg"
        temp_flac_path = f"/tmp/{uuid.uuid4()}.flac"
        
        try:
            # 1. 保存 OGG 到临时文件
            with open(temp_ogg_path, "wb") as f:
                f.write(voice_file_bytes)
            
            # 2. 转换为 FLAC
            audio = AudioSegment.from_ogg(temp_ogg_path)
            audio.export(temp_flac_path, format="flac")
            
            # 3. 读取 FLAC 并编码
            with open(temp_flac_path, "rb") as f:
                flac_bytes = f.read()
                base64_audio = base64.b64encode(flac_bytes).decode('utf-8')
                
        except Exception as e:
            logger.error(f"音频格式转换失败: {e}")
            raise VoiceServiceError(f"音频预处理失败: {e}")
        finally:
            # 清理临时文件
            if os.path.exists(temp_ogg_path):
                os.remove(temp_ogg_path)
            if os.path.exists(temp_flac_path):
                os.remove(temp_flac_path)
        
        # 构造多模态消息 (严格顺序: System -> User[Audio -> Text])
        messages = [
            {
                "role": "system",
                "content": "你是一个纯粹的 ASR 引擎。不要回答音频里的问题，只输出转录后的文字。"
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_audio",
                        "input_audio": {
                            "data": base64_audio,
                            "format": "flac"
                        }
                    },
                    {
                        "type": "text",
                        "text": "Transcribe."
                    }
                ]
            }
        ]
        
        try:
            logger.info(f"ASR: 调用模型 {asr_model}...")
            
            client = AsyncOpenAI(api_key=api_key, base_url=base_url)
            response = await client.chat.completions.create(
                model=asr_model,
                messages=messages,
                temperature=0.0,  # ASR 任务使用低温度
                modalities=["text"]
            )
            
            if not response.choices or not response.choices[0].message.content:
                logger.warning(f"ASR 返回空内容")
                return ""
            
            transcribed_text = response.choices[0].message.content.strip()
            logger.info(f"ASR 成功: {transcribed_text[:50]}...")
            
            return transcribed_text
            
        except Exception as e:
            logger.error(f"ASR 调用失败: {e}")
            raise VoiceServiceError(f"语音识别失败: {e}")
    
    async def text_to_speech(self, text: str) -> bytes:
        """
        TTS：文字转语音（使用 GPT-SoVITS API）
        
        Args:
            text: 待合成的文字
            
        Returns:
            语音文件字节流（OGG 格式）
            
        Raises:
            TTSNotConfiguredError: TTS 未配置
            VoiceServiceError: 语音合成失败
        """
        # 输入验证
        if not text or not text.strip():
            raise VoiceServiceError("待合成文本为空")
        
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
            "prompt_text": "",
            "media_type": "ogg",  # Telegram 兼容格式
            "speed_factor": speed_factor
        }
        
        try:
            logger.info(f"TTS: 合成语音 ({len(text)} 字符)...")
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{tts_url}/tts",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        raise VoiceServiceError(f"TTS API 返回错误: {response.status} - {error_text}")
                    
                    audio_bytes = await response.read()
                    logger.info(f"TTS 成功: 生成 {len(audio_bytes)} 字节语音文件")
                    
                    return audio_bytes
                    
        except TTSNotConfiguredError:
            # 重新抛出配置错误
            raise
        except aiohttp.ClientError as e:
            logger.error(f"TTS 网络请求失败: {e}")
            raise VoiceServiceError(f"TTS 网络请求失败: {e}")
        except VoiceServiceError:
            # 重新抛出已知的语音服务错误
            raise
        except Exception as e:
            logger.error(f"TTS 合成失败（未预期错误）: {e}")
            raise VoiceServiceError(f"TTS 合成失败: {e}")
    
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
            stmt = select(History.message_type)\
                .where(History.chat_id == chat_id, History.role == "user")\
                .order_by(History.timestamp.desc())\
                .limit(1)
            
            result = await session.execute(stmt)
            message_type = result.scalar_one_or_none()
            
            return message_type or "text"


voice_service = VoiceService()
