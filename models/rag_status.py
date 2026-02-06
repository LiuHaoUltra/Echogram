"""RAG 索引状态模型"""

from datetime import datetime
from sqlalchemy import BigInteger, Text, String, DateTime, Index, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from models.base import Base

class RagStatus(Base):
    """
    RAG 索引状态追踪表
    用于替代 Zero Vector Hack，解耦状态记录与向量存储。
    """
    __tablename__ = "rag_status"

    msg_id: Mapped[int] = mapped_column(ForeignKey("history.id", ondelete="CASCADE"), primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False) # 'HEAD', 'TAIL', 'SKIPPED'
    denoised_content: Mapped[str] = mapped_column(Text, nullable=True) # The cleaned Knowledge Fact
    processed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<RagStatus(msg_id={self.msg_id}, status='{self.status}')>"
