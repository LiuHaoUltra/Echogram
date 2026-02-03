"""对话历史记录模型"""

from datetime import datetime
from sqlalchemy import BigInteger, Text, String, DateTime, Index
from sqlalchemy.orm import Mapped, mapped_column
from models.base import Base

class History(Base):
    """对话历史记录表"""
    __tablename__ = "history"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=True)  # Telegram 消息 ID
    reply_to_id: Mapped[int] = mapped_column(BigInteger, nullable=True)  # 被回复的消息 ID
    reply_to_content: Mapped[str] = mapped_column(Text, nullable=True)  # 被回复消息内容摘要
    role: Mapped[str] = mapped_column(String(20))  # 角色: user/assistant/system
    message_type: Mapped[str] = mapped_column(String(10), default="text")  # 消息类型: text/voice/image
    file_id: Mapped[str] = mapped_column(String(255), nullable=True)  # Telegram File ID (用于 Vision/Voice)
    content: Mapped[str] = mapped_column(Text)  # 消息内容
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    # 复合索引：按聊天和时间查询
    __table_args__ = (
        Index('idx_chat_timestamp', 'chat_id', 'timestamp'),
    )

    def __repr__(self):
        return f"<History(chat={self.chat_id}, role='{self.role}', time='{self.timestamp}')>"
