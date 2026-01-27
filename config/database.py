from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from config.settings import settings
from models.base import Base

# 异步引擎
engine = create_async_engine(
    settings.DB_URL,
    echo=False, # True 查看 SQL
    future=True
)

# 异步会话工厂
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False
)

async def init_db():
    """初始化数据库：创建所有表"""
    async with engine.begin() as conn:
        # Dev: create_all
        await conn.run_sync(Base.metadata.create_all)

async def get_db_session():
    """获取数据库会话生成器 (Dependency)"""
    async with AsyncSessionLocal() as session:
        yield session
