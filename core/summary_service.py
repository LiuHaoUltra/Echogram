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
                logger.info(f"Summary status found for {chat_id}: last_id={summary.last_summarized_msg_id}")
                return {
                    "last_id": summary.last_summarized_msg_id,
                    "updated_at": summary.updated_at
                }
            logger.info(f"Summary status NOT found for {chat_id}")
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
            logger.info(f"Summary check skipped for {chat_id}: Already processing.")
            return

        # 防抖 (5s)
        now = time.time()
        last_time = self._last_check.get(chat_id, 0)
        cooldown = 5
        if now - last_time < cooldown:
            logger.info(f"Summary check skipped for {chat_id}: Cooldown ({int(now - last_time)}s < {cooldown}s).")
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
            
            # T = 对话记忆长度 (活跃窗口)
            T = int(configs.get("history_tokens", settings.HISTORY_WINDOW_TOKENS))
            idle_seconds = int(configs.get("summary_idle_seconds", settings.SUMMARY_IDLE_SECONDS))

            # 1. 获取全量未总结消息 (从 last_id 之后开始)
            stmt = select(UserSummary).where(UserSummary.chat_id == chat_id)
            result = await session.execute(stmt)
            user_summary = result.scalar_one_or_none()
            
            last_id = user_summary.last_summarized_msg_id if user_summary else 0
            old_summary = user_summary.content if user_summary else ""

            # 获取该 Chat 的所有消息以计算活跃窗口
            stmt_all = select(History).where(History.chat_id == chat_id).order_by(History.id.desc())
            result_all = await session.execute(stmt_all)
            all_msgs = result_all.scalars().all()
            
            if not all_msgs: return

            # 2. 识别活跃窗口 (Active Window)
            # 从后往前数，直到满 T tokens
            active_ids = set()
            curr_tokens = 0
            win_start_id = all_msgs[0].id # 默认为最后一条
            
            # 这里的顺序是 desc
            for msg in all_msgs:
                msg_text = f"{msg.role}: {msg.content}\n"
                t = history_service.count_tokens(msg_text)
                if curr_tokens + t > T and curr_tokens > 0:
                    break
                curr_tokens += t
                active_ids.add(msg.id)
                win_start_id = msg.id

            # 3. 识别缓冲区 (Buffer)
            # Buffer = 已经在 last_id 之后，但不在活跃窗口内的消息
            buffer_msgs = [m for m in reversed(all_msgs) if last_id < m.id < win_start_id]
            
            if not buffer_msgs:
                logger.info(f"Summary Check for {chat_id}: Buffer is empty (All messages are in Active Window).")
                return

            text_buffer = ""
            for msg in buffer_msgs:
                text_buffer += f"{msg.role}: {msg.content}\n"
            
            buffer_tokens = history_service.count_tokens(text_buffer)
            
            # 4. 检查触发条件
            last_msg_time = all_msgs[0].timestamp
            idle_delta = (datetime.utcnow() - last_msg_time).total_seconds()
            is_idle = idle_delta > idle_seconds
            
            # 触发条件：缓冲区满 T，或者处于闲置状态
            should_summarize = (buffer_tokens >= T) or (is_idle and buffer_tokens > 0)
            
            logger.info(f"Summary Check for {chat_id}: Buffer={buffer_tokens}/{T}, Idle={idle_delta:.0f}/{idle_seconds}, ShouldTrigger={should_summarize}")

            if should_summarize:
                # 仅对缓冲区内容进行归档
                summary_model = configs.get("summary_model_name")
                new_summary = await self._run_llm_summary(old_summary, text_buffer, model_name=summary_model)
                if new_summary:
                    # 更新数据库，指针指向缓冲区最后一条消息
                    final_id = buffer_msgs[-1].id
                    stmt_upsert = insert(UserSummary).values(
                        chat_id=chat_id,
                        content=new_summary,
                        last_summarized_msg_id=final_id,
                        updated_at=datetime.utcnow()
                    ).on_conflict_do_update(
                        index_elements=['chat_id'],
                        set_={
                            "content": new_summary, 
                            "last_summarized_msg_id": final_id,
                            "updated_at": datetime.utcnow()
                        }
                    )
                    await session.execute(stmt_upsert)
                    await session.commit()
                    logger.info(f"Successfully ARCHIVED buffer for {chat_id}. Buffer Tokens: {buffer_tokens}. New Pointer: {final_id}")

    async def _run_llm_summary(self, old_summary: str, new_content: str, model_name: str = None) -> str:
        """调用 LLM 生成新摘要"""
        system_prompt = (
            "你是一个专业的会话记忆管家。你的任务是将新发生的对话片段整合进用户的【长期记忆摘要】中。\n\n"
            "### 记录原则 (Priority):\n"
            "1. **硬核事实 (Core Facts)**：记录用户的具体经历、职业、地理位置、提到的专有名词、重要的日期。 \n"
            "2. **交互坐标 (Interaction State)**：记录当前对话的背景。例如：正在进行的扮演游戏（包括角色设定）、Bot 的特殊回复规则、长期讨论的特定项目。\n"
            "3. **偏好与忌讳 (Preferences)**：记录用户对特定话题的明确态度。 \n"
            "4. **剔除冗余**：不记录“用户互动积极”、“聊天愉快”等感悟类评价。 \n\n"
            "### 格式要求 (Formatting):\n"
            "- **高度压缩**：使用陈述句。如果新信息有冲突，以新信息为准。\n"
            "- **字数控制**：生成的摘要全长必须控制在 **500个字符** 以内，确保信息的“黄金密度”。\n"
            "- **语言**：必须使用中文。输出纯文本，谢绝 Markdown。"
        )

        user_prompt = f"Old Summary:\n{old_summary}\n\nNew Interaction:\n{new_content}"
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        target_model = model_name or settings.SUMMARY_MODEL
        return await simple_chat(target_model, messages, temperature=0.3)

summary_service = SummaryService()
