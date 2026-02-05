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
# @event.listens_for(pool.Pool, "connect") <-- REMOVED
def _get_std_connection(conn):
    """
    递归解包以获取原始的 sqlite3.Connection 对象
    SQLAlchemy Adapter -> aiosqlite.Connection -> sqlite3.Connection
    """
    import sqlite3
    
    # 0. 已经是目标
    if isinstance(conn, sqlite3.Connection):
        return conn
    
    # 1. SQLAlchemy AsyncAdapt_aiosqlite_connection
    if hasattr(conn, "_connection"):
        # 递归调用，因为 _connection 可能是 aiosqlite.Connection
        return _get_std_connection(conn._connection)
        
    # 2. aiosqlite.Connection
    if hasattr(conn, "_conn"):
        return _get_std_connection(conn._conn)
        
    # 3. Generic Driver Connection
    if hasattr(conn, "driver_connection"):
        return _get_std_connection(conn.driver_connection)
        
    return conn

# 监听连接池事件 (更为底层，确保能捕获)
@event.listens_for(pool.Pool, "connect")
def load_extensions(dbapi_conn, conn_record):
    try:
        real_conn = _get_std_connection(dbapi_conn)
        
        if hasattr(real_conn, "enable_load_extension"):
            real_conn.enable_load_extension(True)
            sqlite_vec.load(real_conn)
            real_conn.enable_load_extension(False)
        else:
            import sys
            sys.stderr.write(f"⚠️ [Pool] Connection {type(real_conn)} has no enable_load_extension\n")
            
    except Exception as e:
        import sys
        sys.stderr.write(f"❌ [Pool] Error loading sqlite-vec extension: {e}\n")

# 创建异步会话工厂
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False
)

def _load_vec_sync(conn):
    """同步上下文中手动加载扩展 (用于 init_db)"""
    import sys
    try:
        # conn 是 SQLAlchemy ConnectionWrapper
        # conn.connection 是 Adapter
        wrapper = conn.connection.dbapi_connection
        real_conn = _get_std_connection(wrapper)
        
        if hasattr(real_conn, "enable_load_extension"):
            real_conn.enable_load_extension(True)
            sqlite_vec.load(real_conn)
            real_conn.enable_load_extension(False)
            sys.stderr.write(f"✅ sqlite-vec loaded successfully in sync context.\n")
        else:
            sys.stderr.write(f"⚠️ [Sync] Connection {type(real_conn)} lacks enable_load_extension.\n")
            
    except Exception as e:
        sys.stderr.write(f"❌ Failed to load sqlite-vec extension in sync: {e}\n")
        # Explicit re-raise to crash early if RAG is essential
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
