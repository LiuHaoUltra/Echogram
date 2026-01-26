from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase

class Base(AsyncAttrs, DeclarativeBase):
    """
    SQLAlchemy 声明式基类
    所有模型都应继承此类
    """
    pass
