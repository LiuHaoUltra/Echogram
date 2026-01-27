from sqlalchemy import select, delete
from sqlalchemy.dialects.sqlite import insert
from config.database import get_db_session
from models.whitelist import Whitelist

class AccessService:
    @staticmethod
    async def add_whitelist(chat_id: int, type_: str, description: str = None):
        async for session in get_db_session():
            # 使用 UPSERT 逻辑，如果已存在则更新描述
            stmt = insert(Whitelist).values(
                chat_id=chat_id, 
                type=type_, 
                description=description
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=['chat_id'],
                set_=dict(description=description, type=type_)
            )
            await session.execute(stmt)
            await session.commit()

    @staticmethod
    async def remove_whitelist(chat_id: int):
        async for session in get_db_session():
            await session.execute(delete(Whitelist).where(Whitelist.chat_id == chat_id))
            await session.commit()

    @staticmethod
    async def factory_reset():
        """清空所有白名单"""
        async for session in get_db_session():
            await session.execute(delete(Whitelist))
            await session.commit()

    @staticmethod
    async def get_all_whitelist():
        async for session in get_db_session():
            result = await session.execute(select(Whitelist))
            return result.scalars().all()

    @staticmethod
    async def is_whitelisted(chat_id: int) -> bool:
        async for session in get_db_session():
            result = await session.execute(select(Whitelist).where(Whitelist.chat_id == chat_id))
            return result.scalar_one_or_none() is not None

access_service = AccessService()
