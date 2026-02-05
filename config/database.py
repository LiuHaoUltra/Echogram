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
        except Exception as e:
            # 这里不能轻易打印 log，因为 logger 可能还未初始化完全，或者会有大量刷屏
            # 但为了调试 RAG 问题，我们打印到 stderr
            import sys
            sys.stderr.write(f"Error loading sqlite-vec extension: {e}\n")

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
            # print("✅ sqlite-vec loaded successfully via _load_vec_sync")
    except Exception as e:
        import logging
        logging.getLogger("echogram").error(f"❌ Failed to load sqlite-vec extension: {e}", exc_info=True)
        # 不要吞掉异常，否则后面建表会报错 'no such module'
        # 但考虑到某些环境可能不兼容，我们可以选择 log 后让它挂在 SQL 执行处，或者在这里就抛出
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
