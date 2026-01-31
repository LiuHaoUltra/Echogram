"""记忆摘要模型：对话摘要与用户画像"""

from datetime import datetime
from sqlalchemy import BigInteger, Text, DateTime, Index, func
from sqlalchemy.orm import Mapped, mapped_column
from models.base import Base

class ConversationSummary(Base):
    """对话摘要表 - 中期记忆"""
    __tablename__ = "conversation_summaries"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    
    summary: Mapped[str] = mapped_column(Text)  # 摘要内容
    
    # 摘要覆盖的消息范围
    start_msg_id: Mapped[int] = mapped_column(BigInteger, nullable=True)
    end_msg_id: Mapped[int] = mapped_column(BigInteger, nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index('idx_summary_chat_created', 'chat_id', 'created_at'),
    )

    def __repr__(self):
        return f"<ConversationSummary(chat={self.chat_id}, range={self.start_msg_id}-{self.end_msg_id})>"

class UserSummary(Base):
    """用户画像表 - 长期记忆"""
    __tablename__ = "user_summaries"
    
    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    content: Mapped[str] = mapped_column(Text, default="")  # 用户画像内容
    last_summarized_msg_id: Mapped[int] = mapped_column(BigInteger, default=0)  # 摘要进度指针
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<UserSummary(chat_id={self.chat_id}, updated_at='{self.updated_at}')>"
