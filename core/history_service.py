from sqlalchemy import select, delete
from config.database import get_db_session
from models.history import History

class HistoryService:
    @staticmethod
    async def clear_history(chat_id: int):
        """清空指定会话的记忆"""
        async for session in get_db_session():
            await session.execute(delete(History).where(History.chat_id == chat_id))
            await session.commit()

    @staticmethod
    async def add_message(chat_id: int, role: str, content: str, message_id: int = None, reply_to_id: int = None, reply_to_content: str = None):
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

    @staticmethod
    async def get_recent_context(chat_id: int, limit: int = 10):
        """获取最近 N 条记录 (正序返回)"""
        async for session in get_db_session():
            # 先倒序取最近的，再反转为正序
            stmt = select(History)\
                .where(History.chat_id == chat_id)\
                .order_by(History.timestamp.desc())\
                .limit(limit)
            
            result = await session.execute(stmt)
            history = result.scalars().all()
            return list(reversed(history))

history_service = HistoryService()
