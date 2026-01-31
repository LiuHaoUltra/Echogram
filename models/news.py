"""新闻订阅模型：订阅源与聊天绑定"""

from sqlalchemy import String, Boolean, DateTime, Integer, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column
from models.base import Base
from datetime import datetime

class NewsSubscription(Base):
    """新闻订阅源表"""
    __tablename__ = "news_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    route: Mapped[str] = mapped_column(String(255), unique=True, comment="TG 频道用户名或路由")
    name: Mapped[str] = mapped_column(String(50), comment="订阅源名称")
    last_publish_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, comment="最后推送时间")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否启用")
    
    # 状态监控
    status: Mapped[str] = mapped_column(String(20), default="normal", comment="状态：normal/error")
    last_check_time: Mapped[datetime] = mapped_column(DateTime, nullable=True, comment="最后检查时间")
    last_error: Mapped[str] = mapped_column(Text, nullable=True, comment="最后错误信息")
    error_count: Mapped[int] = mapped_column(Integer, default=0, comment="连续错误次数")

    def __repr__(self):
        return f"<NewsSubscription(name='{self.name}', route='{self.route}', status='{self.status}')>"

class ChatSubscription(Base):
    """聊天订阅关联表 - 实现群组与新闻源的 N:N 绑定"""
    __tablename__ = "chat_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(Integer, index=True, comment="TG 聊天 ID")
    subscription_id: Mapped[int] = mapped_column(ForeignKey("news_subscriptions.id"), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
