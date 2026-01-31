"""白名单模型：允许访问的群组/用户"""

from datetime import datetime
from sqlalchemy import BigInteger, String, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from models.base import Base

class Whitelist(Base):
    """访问白名单表"""
    __tablename__ = "whitelist"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    type: Mapped[str] = mapped_column(String(20))  # 类型: group/user
    description: Mapped[str] = mapped_column(String(100), nullable=True)  # 备注说明
    added_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<Whitelist(id={self.chat_id}, type='{self.type}')>"
