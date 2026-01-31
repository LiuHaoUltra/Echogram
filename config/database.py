"""数据库异步引擎与会话管理"""

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from config.settings import settings
from models.base import Base

# 创建异步引擎
engine = create_async_engine(
    settings.DB_URL,
    echo=False,  # 设为 True 可查看 SQL 日志
    future=True
)

# 创建异步会话工厂
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False
)

async def init_db():
    """初始化数据库：创建所有表"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def get_db_session():
    """数据库会话生成器"""
    async with AsyncSessionLocal() as session:
        yield session
