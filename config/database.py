from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from config.settings import settings
from models.base import Base

# 创建异步引擎
engine = create_async_engine(
    settings.DB_URL,
    echo=False, # 设置为 True 可查看生成的 SQL
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
        # 在生产环境中，这里应该使用 Alembic 进行迁移
        # 开发初期直接 create_all 用于快速迭代
        await conn.run_sync(Base.metadata.create_all)

async def get_db_session():
    """获取数据库会话生成器 (Dependency)"""
    async with AsyncSessionLocal() as session:
        yield session
