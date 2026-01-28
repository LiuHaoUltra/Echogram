import os
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

class Settings:
    # Telegram
    TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
    ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", 0))
    BOT_NAME = os.getenv("BOT_NAME", "Echogram")

    # Database
    # 默认路径
    DB_PATH = os.getenv("DB_PATH", "data/echogram.db")
    DB_URL = f"sqlite+aiosqlite:///{DB_PATH}"

    # Logging
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

    # 记忆系统
    # 窗口大小 (Token)
    HISTORY_WINDOW_TOKENS = int(os.getenv("HISTORY_WINDOW_TOKENS", 6000))
    # 触发阈值 (Token)
    SUMMARY_TRIGGER_TOKENS = int(os.getenv("SUMMARY_TRIGGER_TOKENS", 2000))
    # 闲置阈值 (秒)
    SUMMARY_IDLE_SECONDS = int(os.getenv("SUMMARY_IDLE_SECONDS", 10800))
    # 摘要模型
    SUMMARY_MODEL = os.getenv("SUMMARY_MODEL", "gpt-4o-mini")

    # RSSHub
    RSSHUB_HOST = os.getenv("RSSHUB_HOST", "http://rsshub:1200")

    @classmethod
    def validate(cls):
        if not cls.TG_BOT_TOKEN:
            raise ValueError("TG_BOT_TOKEN is not set in .env")
        if not cls.ADMIN_USER_ID:
            raise ValueError("ADMIN_USER_ID is not set in .env")

settings = Settings()
