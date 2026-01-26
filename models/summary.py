from datetime import datetime
from sqlalchemy import BigInteger, Text, DateTime, Index
from sqlalchemy.orm import Mapped, mapped_column
from models.base import Base

class ConversationSummary(Base):
    """
    对话摘要表 (Episodic Summary)
    属于 "中期记忆"，由后台服务定期生成。
    """
    __tablename__ = "conversation_summaries"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    
    # 摘要内容 (包含 Facts, Narrative, Mood)
    summary: Mapped[str] = mapped_column(Text)
    
    # 该摘要覆盖的消息范围 (History.id)
    # 用于避免重复总结
    start_msg_id: Mapped[int] = mapped_column(BigInteger, nullable=True)
    end_msg_id: Mapped[int] = mapped_column(BigInteger, nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index('idx_summary_chat_created', 'chat_id', 'created_at'),
    )

    def __repr__(self):
        return f"<ConversationSummary(chat={self.chat_id}, range={self.start_msg_id}-{self.end_msg_id})>"
