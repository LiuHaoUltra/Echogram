"""SQLAlchemy 基类与通用工具函数"""

from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase
from datetime import datetime, timezone

class Base(AsyncAttrs, DeclarativeBase):
    """ORM 模型基类"""
    pass

def get_utc_now() -> datetime:
    """获取当前 UTC 时间（Naive）"""
    return datetime.now(timezone.utc).replace(tzinfo=None)
