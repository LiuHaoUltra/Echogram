import asyncio
import re
from telegram import Update, constants, ReactionTypeEmoji
from telegram.ext import ContextTypes
from core.history_service import history_service
from core.media_service import media_service
from utils.logger import logger

class SenderService:
    """
    统一消息发送服务
    负责解析 <chat> 标签、拟人化延迟、表情回应和历史记录持久化
    """
    
    # 表情白名单
    TG_FREE_REACTIONS = {
        "👍", "👎", "❤️", "🔥", "🥰", "👏", "😁", "🤔", "🤯", "😱", 
        "🤬", "😢", "🎉", "🤩", "🤮", "💩", "🙏", "👌", "🕊️", "🤡", 
        "🥱", "🥴", "😍", "🐳", "❤️‍🔥", "🌚", "🌭", "💯", "🤣", "⚡", 
        "🍌", "🏆", "💔", "🤨", "😐", "🍓", "🍾", "💋", "🖕", "😈", 
        "😴", "😭", "🤓", "👻", "👨‍💻", "👀", "🎃", "🙈", "😇", "😨", 
        "🤝", "✍️", "✍", "🤗", "🫡", "🎅", "🎄", "☃️", "💅", "🤪", "🗿", 
        "🆒", "💘", "🙉", "🦄", "😘", "💊", "🙊", "😎", "👾", "🤷‍♂️", 
        "🤷", "🤷‍♀️", "😡"
    }

    async def send_llm_reply(self, chat_id: int, reply_content: str, context: ContextTypes.DEFAULT_TYPE, history_msgs: list = None, message_type: str = 'text'):
        """
        解析 LLM 输出并发送消息
        :param chat_id: 目标会话 ID
        :param reply_content: LLM 生成的原始内容 (带标签)
        :param context: Telegram Context
        :param history_msgs: 历史消息列表 (用于兜底表情回应目标)
        :param message_type: 'text' 或 'voice'。若为 'voice' 且 ASR/TTS 已配置，则发送语音。
        """
        if message_type == 'text':
            # 强制过滤转录标签 (防止模型在文字模式下误触语音协议产生转录块)
            reply_content = re.sub(r"<transcript>.*?</transcript>", "", reply_content, flags=re.DOTALL).strip()

        # 1. 解析标签
        tag_pattern = r"<chat(?P<attrs>[^>]*)>(?P<content>.*?)</chat>"
        matches = list(re.finditer(tag_pattern, reply_content, flags=re.DOTALL))
        
        reply_blocks = []
        cleaned_history_parts = []

        if not matches:
            # 兜底处理无标签情况
            reply_blocks.append({"content": reply_content.strip(), "reply": None, "react": None})
            cleaned_history_parts.append(f"<chat>{reply_content.strip()}</chat>")
        else:
            for m in matches:
                attrs_raw = m.group("attrs")
                content = m.group("content").strip()
                
                reply_id = None
                react_emoji = None
                
                # 解析属性
                reply_match = re.search(r'reply=["\'](\d+)["\']', attrs_raw)
                if reply_match:
                    reply_id = int(reply_match.group(1))
                    
                react_match = re.search(r'react=["\']([^"\']+)["\']', attrs_raw)
                if react_match:
                    react_emoji = react_match.group(1).strip()
                
                # 清洗表情（仅用于历史记录）
                valid_react_for_history = None
                if react_emoji:
                    emoji_to_check = react_emoji.split(":")[0].strip() if ":" in react_emoji else react_emoji
                    if emoji_to_check in self.TG_FREE_REACTIONS:
                        valid_react_for_history = react_emoji
                
                # 构建清洗后的标签用于保存
                attr_str = ""
                if reply_id: attr_str += f' reply="{reply_id}"'
                if valid_react_for_history: attr_str += f' react="{valid_react_for_history}"'
                cleaned_history_parts.append(f"<chat{attr_str}>{content}</chat>")

                if content or react_emoji:
                    reply_blocks.append({
                        "content": content if content else "...",
                        "reply": reply_id,
                        "react": react_emoji
                    })

        cleaned_reply_content = "\n".join(cleaned_history_parts)

        # 2. 依次发送块
        last_sent_msg_id = None
        for i, block in enumerate(reply_blocks):
            content = block["content"]
            target_reply_id = block["reply"]
            target_react_emoji = block["react"]

            # 处理表情回应
            if target_react_emoji:
                await self._handle_reaction(chat_id, target_react_emoji, target_reply_id, history_msgs, context)

            # 处理消息发送
            if not content or content == "...":
                continue

            # 拟人化延迟 (文字模式显示 Typing，语音模式显示 Record Voice)
            if i > 0:
                await asyncio.sleep(1.0)
            
            if message_type == 'voice' and await media_service.is_tts_configured():
                # --- 语音模式发送 ---
                # 清洗文本 (移除所有 XML 标签，防止 TTS 读出标签)
                clean_text = re.sub(r'<[^>]+>', '', content).strip()
                if not clean_text: continue

                # 拟人化时长 (根据文字长度模拟录音时间)
                rec_duration = min(len(clean_text) * 0.2, 5.0)
                await context.bot.send_chat_action(chat_id=chat_id, action=constants.ChatAction.RECORD_VOICE)
                await asyncio.sleep(rec_duration)

                try:
                    voice_bytes = await media_service.text_to_speech(clean_text)
                    await context.bot.send_chat_action(chat_id=chat_id, action=constants.ChatAction.UPLOAD_VOICE)
                    
                    import time
                    sent_msg = await context.bot.send_voice(
                        chat_id=chat_id,
                        voice=voice_bytes,
                        filename=f"voice_{int(time.time())}_{i}.ogg",
                        reply_to_message_id=target_reply_id
                    )
                    last_sent_msg_id = sent_msg.message_id
                except Exception as e:
                    logger.error(f"SenderService: TTS Failed, falling back to text: {e}")
                    sent_msg = await context.bot.send_message(chat_id=chat_id, text=clean_text, reply_to_message_id=target_reply_id)
                    last_sent_msg_id = sent_msg.message_id
            else:
                # --- 文字模式发送 ---
                typing_duration = min(len(content) * 0.15, 3.0)
                await context.bot.send_chat_action(chat_id=chat_id, action=constants.ChatAction.TYPING)
                await asyncio.sleep(typing_duration)

                try:
                    sent_msg = await context.bot.send_message(
                        chat_id=chat_id, 
                        text=content, 
                        reply_to_message_id=target_reply_id
                    )
                    last_sent_msg_id = sent_msg.message_id
                except Exception as e:
                    logger.warning(f"SenderService: Failed to send part {i} to {chat_id}: {e}")
                    if target_reply_id: # 降级不带引用重试
                        try:
                            sent_msg = await context.bot.send_message(chat_id=chat_id, text=content)
                            last_sent_msg_id = sent_msg.message_id
                        except: pass
        
        # 3. 记录历史
        await history_service.add_message(
            chat_id, "assistant", cleaned_reply_content, 
            message_id=last_sent_msg_id
        )
        
        # 4. 触发总结检查
        try:
            from core.summary_service import summary_service
            asyncio.create_task(summary_service.check_and_summarize(chat_id))
        except Exception as e:
            logger.error(f"SenderService: Failed to trigger summary for {chat_id}: {e}")

    async def _handle_reaction(self, chat_id: int, react_emoji: str, target_reply_id: int, history_msgs: list, context: ContextTypes.DEFAULT_TYPE):
        """处理表情回应逻辑"""
        react_id = None
        react_emoji_part = react_emoji
        if ":" in react_emoji:
            parts = react_emoji.split(":", 1)
            react_emoji_part = parts[0].strip()
            try:
                react_id = int(parts[1].strip())
            except: pass

        if react_emoji_part not in self.TG_FREE_REACTIONS:
            logger.warning(f"SenderService: Reaction '{react_emoji_part}' not in whitelist.")
            return

        try:
            # 确定目标 ID
            react_target_id = react_id or target_reply_id
            if not react_target_id and history_msgs:
                # 兼容字典和模型对象
                last_user_msg = None
                for m in reversed(history_msgs):
                    role = m.get('role') if isinstance(m, dict) else getattr(m, 'role', None)
                    if role == 'user':
                        last_user_msg = m
                        break
                
                if last_user_msg:
                    react_target_id = last_user_msg.get('message_id') if isinstance(last_user_msg, dict) else getattr(last_user_msg, 'message_id', None)
            
            if react_target_id:
                await context.bot.set_message_reaction(
                    chat_id=chat_id,
                    message_id=react_target_id,
                    reaction=[ReactionTypeEmoji(react_emoji_part)]
                )
        except Exception as e:
            logger.warning(f"SenderService: Failed to set reaction on MSG {react_target_id}: {e}")

sender_service = SenderService()
