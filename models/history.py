from datetime import datetime
from sqlalchemy import BigInteger, Text, String, DateTime, Index
from sqlalchemy.orm import Mapped, mapped_column
from models.base import Base

class History(Base):
    """
    对话历史表
    用于构建滚动上下文 (Context Window)
    """
    __tablename__ = "history"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=True) # Telegram Message ID
    reply_to_id: Mapped[int] = mapped_column(BigInteger, nullable=True) # ID of the message being replied to
    reply_to_content: Mapped[str] = mapped_column(Text, nullable=True) # Content of the referenced message
    role: Mapped[str] = mapped_column(String(20)) # 'user' or 'assistant'
    content: Mapped[str] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    # 复合索引优化查询：按 chat_id 和 timestamp 倒序查找
    __table_args__ = (
        Index('idx_chat_timestamp', 'chat_id', 'timestamp'),
    )

    def __repr__(self):
        return f"<History(chat={self.chat_id}, role='{self.role}', time='{self.timestamp}')>"
