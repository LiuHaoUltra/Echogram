from datetime import datetime
from sqlalchemy import BigInteger, String, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from models.base import Base

class Whitelist(Base):
    """
    白名单表
    控制哪些用户或群组可以触发 Bot
    """
    __tablename__ = "whitelist"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    type: Mapped[str] = mapped_column(String(20))  # 'group' or 'user'
    description: Mapped[str] = mapped_column(String(100), nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<Whitelist(id={self.chat_id}, type='{self.type}')>"
