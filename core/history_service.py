import tiktoken
from sqlalchemy import select, delete, update
from config.database import get_db_session
from models.history import History
from sqlalchemy import select, desc, delete, func, update, and_
from utils.logger import logger
from typing import Optional

class HistoryService:
    _encoding = None
    
    # Class-level cache applied
    def __init__(self):
        # Encoding 单例

        if HistoryService._encoding is None:
            HistoryService._encoding = tiktoken.get_encoding("cl100k_base")

    def count_tokens(self, text: str) -> int:
        if not text: return 0
        return len(self._encoding.encode(text))

    def _truncate_content(self, text: str, char_limit: int = 6000) -> str:
        """
        物理截断：保留头尾，中间替换
        :param char_limit: 字符限制（非 Token）
        """
        if not text or len(text) < char_limit:
            return text
        
        # 保留头尾各 30% 的内容
        keep = int(char_limit * 0.3)
        return f"{text[:keep]}\n\n[... Content Truncated due to safety limit ({len(text)} chars) ...]\n\n{text[-keep:]}"

    async def clear_history(self, chat_id: int):
        """清空指定会话的记忆"""
        async for session in get_db_session():
            await session.execute(delete(History).where(History.chat_id == chat_id))
            await session.commit()

    async def factory_reset(self):
        """清空所有历史记录"""
        async for session in get_db_session():
            await session.execute(delete(History))
            await session.commit()

    async def add_message(
        self,
        chat_id: int,
        role: str,
        content: str,
        message_id: int = None,
        message_type: str = "text",
        reply_to_id: int = None,
        reply_to_content: str = None,
        file_id: str = None,
    ) -> History:
        """添加一条新消息到历史记录（兼容 reply/file 扩展字段）"""
        async for session in get_db_session():
            if message_id:
                # 检查是否已存在 (避免重复)
                stmt = select(History).where(History.chat_id == chat_id, History.message_id == message_id)
                existing = (await session.execute(stmt)).scalar_one_or_none()
                if existing:
                    return existing

            new_msg = History(
                chat_id=chat_id,
                role=role,
                content=content,
                message_id=message_id,
                message_type=message_type,
                reply_to_id=reply_to_id,
                reply_to_content=reply_to_content,
                file_id=file_id,
            )
            session.add(new_msg)
            await session.commit()
            await session.refresh(new_msg)
            return new_msg
            
    async def get_message(self, chat_id: int, message_id: int) -> Optional[History]:
        """根据 TG Message ID 获取消息对象"""
        async for session in get_db_session():
            stmt = select(History).where(History.chat_id == chat_id, History.message_id == message_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def get_message_by_db_id(self, db_id: int, chat_id: int = None) -> Optional[History]:
        """根据 DB Primary Key 获取消息对象"""
        async for session in get_db_session():
            if chat_id:
                stmt = select(History).where(History.id == db_id, History.chat_id == chat_id)
            else:
                stmt = select(History).where(History.id == db_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def update_message_content_by_file_id(self, file_id: str, new_content: str):
        """根据 File ID 更新消息内容 (用于回填摘要)"""
        async for session in get_db_session():
            stmt = update(History).where(History.file_id == file_id).values(content=new_content)
            await session.execute(stmt)
            await session.commit()

    async def get_token_controlled_context(self, chat_id: int, target_tokens: int):
        """
        [核心逻辑] 获取历史，直到填满 target_tokens
        """
        async for session in get_db_session():
            # 预取最近 200 条
            stmt = select(History).where(History.chat_id == chat_id)\
                .order_by(History.timestamp.desc()).limit(200)
            result = await session.execute(stmt)
            candidates = result.scalars().all()

            selected = []
            current_tokens = 0

            # 定义物理强度截断（针对恶意刷内容） 
            # 这里的阈值设为 8000 字符左右，约合 2000-3000 Tokens
            CHAR_HARD_LIMIT = 8000 
            
            for i, msg in enumerate(candidates):
                # 预处理：物理截取单条极长消息
                content = self._truncate_content(msg.content, CHAR_HARD_LIMIT)

                # 估算包含前缀的消息长度
                msg_text = f"[{'MSG ID'}] [{'YYYY-MM-DD HH:MM:SS'}] [{msg.message_type or 'Text'}] {msg.role}: {content}\n"
                cost = self.count_tokens(msg_text)
                
                # 兜底：即使第一条消息就爆了预算，也强行包含它
                if i > 0 and current_tokens + cost > target_tokens:
                    break 
                
                msg.content = content
                selected.append(msg)
                current_tokens += cost

            return list(reversed(selected))

    async def get_session_stats(self, chat_id: int, target_tokens: int, last_summarized_id: int = 0):
        """
        统一计算会话统计数据：活跃窗口 Token、缓冲区 Token
        """
        async for session in get_db_session():
            stmt = select(History).where(History.chat_id == chat_id).order_by(History.id.desc())
            result = await session.execute(stmt)
            all_msgs = result.scalars().all()

            if not all_msgs:
                return {"active_tokens": 0, "buffer_tokens": 0, "win_start_id": 0, "total_msgs": 0}

            active_tokens = 0
            win_start_id = all_msgs[0].id
            CHAR_HARD_LIMIT = 8000

            # 1. 计算活跃窗口
            for i, m in enumerate(all_msgs):
                content = self._truncate_content(m.content, CHAR_HARD_LIMIT)
                msg_text = f"[{'MSG ID'}] [{'YYYY-MM-DD HH:MM:SS'}] [{m.message_type or 'Text'}] {m.role}: {content}\n"
                t = self.count_tokens(msg_text)
                
                if i > 0 and active_tokens + t > target_tokens:
                    break
                active_tokens += t
                win_start_id = m.id

            # 2. 计算缓冲区 (last_summarized_id -> win_start_id 之间)
            buffer_tokens = 0
            for m in all_msgs:
                if last_summarized_id < m.id < win_start_id:
                    content = self._truncate_content(m.content, CHAR_HARD_LIMIT)
                    msg_text = f"[{'MSG ID'}] [{'YYYY-MM-DD HH:MM:SS'}] [{m.message_type or 'Text'}] {m.role}: {content}\n"
                    buffer_tokens += self.count_tokens(msg_text)

            return {
                "active_tokens": active_tokens,
                "buffer_tokens": buffer_tokens,
                "win_start_id": win_start_id,
                "total_msgs": len(all_msgs)
            }

    async def get_last_message_time(self, chat_id: int):
        """获取最近一条消息的时间"""
        async for session in get_db_session():
            stmt = select(History.timestamp)\
                .where(History.chat_id == chat_id)\
                .order_by(History.timestamp.desc())\
                .limit(1)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()    

    async def update_message_content(self, chat_id: int, message_id: int, new_content: str):
        """
        根据 TG Message ID 更新消息内容
        """
        async for session in get_db_session():
            stmt = update(History).where(History.chat_id == chat_id, History.message_id == message_id).values(content=new_content)
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount > 0

    async def update_message_content_by_db_id(self, db_id: int, new_content: str, chat_id: int = None):
        """
        根据 DB Primary Key 更新消息内容
        可选校验 chat_id 以确保安全性
        """
        async for session in get_db_session():
            if chat_id:
                stmt = update(History).where(History.id == db_id, History.chat_id == chat_id).values(content=new_content)
            else:
                stmt = update(History).where(History.id == db_id).values(content=new_content)
            
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount > 0

    async def delete_message(self, chat_id: int, message_id: int):
        """
        根据 TG Message ID 删除消息
        """
        async for session in get_db_session():
            stmt = delete(History).where(History.chat_id == chat_id, History.message_id == message_id)
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount > 0

    async def delete_message_by_db_id(self, db_id: int, chat_id: int = None):
        """
        根据 DB Primary Key 删除消息
        """
        async for session in get_db_session():
            if chat_id:
                stmt = delete(History).where(History.id == db_id, History.chat_id == chat_id)
            else:
                stmt = delete(History).where(History.id == db_id)

            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount > 0

history_service = HistoryService()
