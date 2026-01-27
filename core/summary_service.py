import asyncio
import time
from datetime import datetime, timedelta
from sqlalchemy import select, delete
from sqlalchemy.dialects.sqlite import insert

from config.settings import settings
from config.database import get_db_session
from models.summary import UserSummary
from models.history import History
from core.history_service import history_service
from core.llm_utils import simple_chat
from utils.logger import logger

class SummaryService:
    def __init__(self):
        self._processing = set() # 正在处理的 chat_id
        self._last_check = {}    # 上次检查时间戳

    async def get_summary(self, chat_id: int) -> str:
        """获取当前用户的长期摘要"""
        async for session in get_db_session():
            stmt = select(UserSummary).where(UserSummary.chat_id == chat_id)
            result = await session.execute(stmt)
            summary = result.scalar_one_or_none()
            return summary.content if summary else ""

    async def get_status(self, chat_id: int):
        """获取总结状态：最后总结的记录 ID 和 更新时间"""
        async for session in get_db_session():
            stmt = select(UserSummary).where(UserSummary.chat_id == chat_id)
            result = await session.execute(stmt)
            summary = result.scalar_one_or_none()
            if summary:
                logger.debug(f"Summary status found for {chat_id}: last_id={summary.last_summarized_msg_id}")
                return {
                    "last_id": summary.last_summarized_msg_id,
                    "updated_at": summary.updated_at
                }
            logger.debug(f"Summary status NOT found for {chat_id}")
            return {"last_id": 0, "updated_at": None}

    async def clear_summary(self, chat_id: int):
        """清空长期摘要"""
        async for session in get_db_session():
            stmt = delete(UserSummary).where(UserSummary.chat_id == chat_id)
            await session.execute(stmt)
            await session.commit()
            logger.info(f"Summary cleared for {chat_id}")

    async def factory_reset(self):
        """清空所有摘要"""
        async for session in get_db_session():
            await session.execute(delete(UserSummary))
            await session.commit()

    async def check_and_summarize(self, chat_id: int):
        """
        触发检查 (Fire-and-forget)
        """
        if chat_id in self._processing: 
            logger.debug(f"Summary check skipped for {chat_id}: Already processing.")
            return

        # 防抖 (5s)
        now = time.time()
        last_time = self._last_check.get(chat_id, 0)
        cooldown = 5
        if now - last_time < cooldown:
            logger.debug(f"Summary check skipped for {chat_id}: Cooldown ({int(now - last_time)}s < {cooldown}s).")
            return

        self._processing.add(chat_id)
        self._last_check[chat_id] = now
        
        try:
            await self._process_summary(chat_id)
        except Exception as e:
            logger.error(f"Summary failed for {chat_id}: {e}")
        finally:
            self._processing.discard(chat_id)

    async def _process_summary(self, chat_id: int):
        async for session in get_db_session():
            # 获取动态配置
            from core.config_service import config_service
            configs = await config_service.get_all_settings()
            
            token_threshold = int(configs.get("history_tokens", settings.SUMMARY_TRIGGER_TOKENS))
            idle_seconds = int(configs.get("summary_idle_seconds", settings.SUMMARY_IDLE_SECONDS))

            # 获取当前摘要
            stmt = select(UserSummary).where(UserSummary.chat_id == chat_id)
            result = await session.execute(stmt)
            user_summary = result.scalar_one_or_none()
            
            last_id = user_summary.last_summarized_msg_id if user_summary else 0
            old_summary = user_summary.content if user_summary else ""

            # 获取新增消息
            stmt_msgs = select(History).where(
                (History.chat_id == chat_id) & 
                (History.id > last_id)
            ).order_by(History.id.asc())
            
            result_msgs = await session.execute(stmt_msgs)
            new_msgs = result_msgs.scalars().all()
            
            if not new_msgs:
                return

            # 计算 Trigger 条件
            text_buffer = ""
            for msg in new_msgs:
                text_buffer += f"{msg.role}: {msg.content}\n"
            
            total_tokens = history_service.count_tokens(text_buffer)
            
            last_msg_time = new_msgs[-1].timestamp
            # 检查空闲时间
            # Assuming utcnow for coherence with models.
            idle_delta = (datetime.utcnow() - last_msg_time).total_seconds()
            is_idle = idle_delta > idle_seconds
            
            # 是否触发
            should_summarize = (total_tokens >= token_threshold) or is_idle
            
            logger.info(f"Summary Check for {chat_id}: Tokens={total_tokens}/{token_threshold}, Idle={idle_delta:.0f}/{idle_seconds}, ShouldTrigger={should_summarize}")

            if should_summarize:
                new_summary = await self._run_llm_summary(old_summary, text_buffer)
                if new_summary:
                    # 更新数据库
                    stmt_upsert = insert(UserSummary).values(
                        chat_id=chat_id,
                        content=new_summary,
                        last_summarized_msg_id=new_msgs[-1].id,
                        updated_at=datetime.utcnow()
                    ).on_conflict_do_update(
                        index_elements=['chat_id'],
                        set_={
                            "content": new_summary, 
                            "last_summarized_msg_id": new_msgs[-1].id,
                            "updated_at": datetime.utcnow()
                        }
                    )
                    await session.execute(stmt_upsert)
                    await session.commit()
                    logger.info(f"Successfully SAVED summary for {chat_id}. Content length: {len(new_summary)}. New Pointer: {new_msgs[-1].id}")

    async def _run_llm_summary(self, old_summary: str, new_content: str) -> str:
        """调用 LLM 生成新摘要"""
        system_prompt = (
            "你是一个专业的记录员。你的任务是维护关于用户的长期记忆摘要（Summary）。\n"
            "输入包括：\n"
            "1. 旧的摘要 (Old Summary)\n"
            "2. 新的对话片段 (New Interaction)\n\n"
            "要求：\n"
            "- 整合新信息到摘要中，更新用户的事实、偏好、性格特征。\n"
            "- 保持摘要简练、客观、高密度。\n"
            "- 如果新对话没有提供有价值的信息，保持原样。\n"
            "- 输出必须纯文本，不要 markdown 格式，不要废话。"
        )

        user_prompt = f"Old Summary:\n{old_summary}\n\nNew Interaction:\n{new_content}"
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        return await simple_chat(settings.SUMMARY_MODEL, messages, temperature=0.3)

summary_service = SummaryService()
