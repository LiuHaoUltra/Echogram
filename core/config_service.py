from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert
from config.database import get_db_session
from models.config import Config

class ConfigService:
    """
    提供对 Config 表的增删改查服务
    """
    
    @staticmethod
    async def get_value(key: str, default: str = None) -> str:
        """获取配置值，不存在则返回默认值"""
        async for session in get_db_session():
            result = await session.execute(select(Config.value).where(Config.key == key))
            value = result.scalar_one_or_none()
            return value if value is not None else default

    @staticmethod
    async def set_value(key: str, value: str):
        """设置配置值 (Upsert)"""
        async for session in get_db_session():
            stmt = insert(Config).values(key=key, value=value)
            # SQLite 的 Upsert 语法
            stmt = stmt.on_conflict_do_update(
                index_elements=['key'],
                set_={'value': value}
            )
            await session.execute(stmt)
            await session.commit()

    @staticmethod
    async def get_all_settings() -> dict:
        """获取所有配置并以字典返回"""
        async for session in get_db_session():
            result = await session.execute(select(Config))
            configs = result.scalars().all()
            return {c.key: c.value for c in configs}

config_service = ConfigService()
