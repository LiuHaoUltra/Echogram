from sqlalchemy import select, func
from config.database import get_db_session
from models.history import History
from models.summary import ConversationSummary
from models.config import Config
from core.config_service import config_service
from openai import AsyncOpenAI
from utils.logger import logger
import json

class MemoryService:
    """
    长期记忆服务
    """
    
    # 总结触发阈值
    SUMMARY_THRESHOLD = 20 
    
    @staticmethod
    async def get_latest_summary(chat_id: int) -> str:
        """获取最新摘要"""
        async for session in get_db_session():
            stmt = select(ConversationSummary.summary)\
                .where(ConversationSummary.chat_id == chat_id)\
                .order_by(ConversationSummary.created_at.desc())\
                .limit(1)
            result = await session.execute(stmt)
            summary = result.scalar_one_or_none()
            return summary if summary else ""

    @staticmethod
    async def get_latest_summary_time(chat_id: int) -> str:
        """获取最新摘要时间"""
        async for session in get_db_session():
            stmt = select(ConversationSummary.created_at)\
                .where(ConversationSummary.chat_id == chat_id)\
                .order_by(ConversationSummary.created_at.desc())\
                .limit(1)
            result = await session.execute(stmt)
            dt = result.scalar_one_or_none()
            
            if not dt:
                return "N/A"
                
            # 获取时区
            import pytz
            tz_str = await config_service.get_value("timezone", "UTC")
            try:
                tz = pytz.timezone(tz_str)
            except:
                tz = pytz.UTC
            
            # 统一转换为 UTC
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=pytz.UTC)
                
            local_dt = dt.astimezone(tz)
            return local_dt.strftime("%m-%d %H:%M")

    @staticmethod
    async def check_and_summarize(chat_id: int):
        """
        检查并生成摘要
        """
        try:
            async for session in get_db_session():
                # 1. 获取最后的摘要尾 ID
                
                last_summary = await session.execute(
                    select(ConversationSummary)
                    .where(ConversationSummary.chat_id == chat_id)
                    .order_by(ConversationSummary.created_at.desc())
                    .limit(1)
                )
                last_summary = last_summary.scalar_one_or_none()
                
                last_covered_id = 0
                if last_summary and last_summary.end_msg_id:
                    last_covered_id = last_summary.end_msg_id
                    
                # 2. 统计未覆盖消息
                stmt = select(func.count()).select_from(History).where(
                    History.chat_id == chat_id,
                    History.id > last_covered_id
                )
                count = (await session.execute(stmt)).scalar()
                
                if count < MemoryService.SUMMARY_THRESHOLD:
                    return # 数量不足，跳过
                    
                # 3. 获取新消息
                msgs_stmt = select(History).where(
                    History.chat_id == chat_id,
                    History.id > last_covered_id
                ).order_by(History.id.asc())
                
                msgs = (await session.execute(msgs_stmt)).scalars().all()
                if not msgs:
                    return

                # 4. LLM 生成
                summary_text = await MemoryService._generate_summary(msgs, last_summary.summary if last_summary else None)
                
                if not summary_text:
                    logger.warning(f"Summarization returned empty for chat {chat_id}")
                    return

                # 5. 保存摘要
                new_summary = ConversationSummary(
                    chat_id=chat_id,
                    summary=summary_text,
                    start_msg_id=msgs[0].id,
                    end_msg_id=msgs[-1].id
                )
                session.add(new_summary)
                await session.commit()
                logger.info(f"Generated new summary for chat {chat_id}, covered {len(msgs)} msgs.")
                
        except Exception as e:
            logger.error(f"Error in check_and_summarize for chat {chat_id}: {e}")

    @staticmethod
    async def _generate_summary(messages: list[History], previous_summary: str = None) -> str:
        """
        调用 LLM 生成摘要
        """
        # 准备 Prompt
        configs = await config_service.get_all_settings()
        api_key = configs.get("api_key")
        base_url = configs.get("api_base_url")
        # 优先使用专用模型
        main_model = configs.get("model_name", "gpt-3.5-turbo")
        summary_model = configs.get("summary_model_name")
        
        # 降级为主模型
        model = summary_model if summary_model and summary_model.strip() else main_model
        
        if not api_key: return None

        # 格式化文本
        conversation_text = ""
        for m in messages:
            role = "User" if m.role == 'user' else "AI"
            conversation_text += f"{role}: {m.content}\n"
            
        system_prompt = (
            "你是专业的对话分析师。你的任务是阅读一段对话，并提取关键的“长期记忆”元数据。\n"
            "不用试图压缩所有对话细节，而是提取以下维度的信息：\n"
            "1. **事实 (Facts)**: 用户提到的具体偏好、职业、使用的工具、做出的决定。\n"
            "2. **叙事 (Narrative)**: 当前讨论的话题进度（如：架构设计已敲定，正在编码）。\n"
            "3. **情绪 (Mood)**: 用户的语气、态度变化。\n\n"
            "如果有之前的摘要，请进行增量更新或合并。请输出一段简洁的Markdown文本，用于让AI在未来快速回忆起背景。"
        )
        
        user_content = f"【待分析对话】\n{conversation_text}"
        if previous_summary:
            user_content = f"【前情提要】\n{previous_summary}\n\n" + user_content
            
        try:
            client = AsyncOpenAI(api_key=api_key, base_url=base_url)
            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content}
                ],
                temperature=0.3
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"LLM Summarization Failed: {e}")
            return None

memory_service = MemoryService()
