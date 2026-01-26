from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column
from models.base import Base

class Config(Base):
    """
    配置存储表 (Key-Value)
    用于存储 API Key, Model Name, System Prompt 等需动态修改的配置
    """
    __tablename__ = "config"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(Text)

    def __repr__(self):
        return f"<Config(key='{self.key}', value='{self.value[:20]}...')>"
