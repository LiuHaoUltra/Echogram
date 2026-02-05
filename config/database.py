from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from config.settings import settings
from models.base import Base
from sqlalchemy import event, text, pool
import sqlite3
import sqlite_vec

# 创建异步引擎
engine = create_async_engine(
    settings.DB_URL,
    echo=False,  # 设为 True 可查看 SQL 日志
    future=True
)

# 监听连接池事件 (更为底层，确保能捕获)
@event.listens_for(pool.Pool, "connect")
def load_extensions(dbapi_conn, conn_record):
    if hasattr(dbapi_conn, "enable_load_extension"):
        try:
            dbapi_conn.enable_load_extension(True)
            sqlite_vec.load(dbapi_conn)
            dbapi_conn.enable_load_extension(False)
        except Exception:
            pass

# 创建异步会话工厂
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False
)

def _load_vec_sync(conn):
    """同步上下文中手动加载扩展 (用于 init_db)"""
    try:
        dbapi_conn = conn.connection.dbapi_connection
        
        if hasattr(dbapi_conn, "enable_load_extension"):
            dbapi_conn.enable_load_extension(True)
            sqlite_vec.load(dbapi_conn)
            dbapi_conn.enable_load_extension(False)
    except Exception:
        pass

async def init_db():
    """初始化数据库：创建所有表及向量虚表"""
    async with engine.begin() as conn:
        # 1. 确保当前用于建表的连接加载了扩展
        await conn.run_sync(_load_vec_sync)
        
        # 2. 创建常规表
        await conn.run_sync(Base.metadata.create_all)
        
        # 3. 创建向量虚表
        await conn.execute(text("""
            CREATE VIRTUAL TABLE IF NOT EXISTS history_vec USING vec0(
                rowid INTEGER PRIMARY KEY, 
                embedding FLOAT[1536]
            );
        """))

async def get_db_session():
    """数据库会话生成器"""
    async with AsyncSessionLocal() as session:
        yield session
