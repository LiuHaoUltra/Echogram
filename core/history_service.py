import tiktoken
from sqlalchemy import select, delete
from config.database import get_db_session
from models.history import History

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

    async def add_message(self, chat_id: int, role: str, content: str, message_id: int = None, reply_to_id: int = None, reply_to_content: str = None):
        """添加一条消息记录"""
        async for session in get_db_session():
            msg = History(
                chat_id=chat_id, 
                role=role, 
                content=content, 
                message_id=message_id,
                reply_to_id=reply_to_id, 
                reply_to_content=reply_to_content
            )
            session.add(msg)
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

            # 贪婪填充
            for msg in candidates:
                # 估算开销
                cost = self.count_tokens(msg.content) + 4
                
                if current_tokens + cost > target_tokens:
                    break # 满了，停止
                
                selected.append(msg)
                current_tokens += cost

            # 恢复时间顺序
            return list(reversed(selected))

    async def calculate_context_usage(self, chat_id: int, target_tokens: int) -> int:
        """
        计算当前上下文实际占用的 Token 数
        """
        async for session in get_db_session():
            stmt = select(History).where(History.chat_id == chat_id)\
                .order_by(History.timestamp.desc()).limit(200)
            result = await session.execute(stmt)
            candidates = result.scalars().all()

            current_tokens = 0
            for msg in candidates:
                cost = self.count_tokens(msg.content) + 4
                if current_tokens + cost > target_tokens:
                    break
                current_tokens += cost
            
            return current_tokens

    async def get_recent_context(self, chat_id: int, limit: int = 10):
        """[Deprecated] 获取最近 N 条记录 (正序返回)"""
        async for session in get_db_session():
            stmt = select(History)\
                .where(History.chat_id == chat_id)\
                .order_by(History.timestamp.desc())\
                .limit(limit)
            
            result = await session.execute(stmt)
            history = result.scalars().all()
            return list(reversed(history))

    async def get_last_message_time(self, chat_id: int):
        """获取最近一条消息的时间"""
        async for session in get_db_session():
            stmt = select(History.timestamp)\
                .where(History.chat_id == chat_id)\
                .order_by(History.timestamp.desc())\
                .limit(1)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

history_service = HistoryService()
