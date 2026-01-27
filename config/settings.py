import os
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()

class Settings:
    # Telegram
    TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
    ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", 0))
    BOT_NAME = os.getenv("BOT_NAME", "Echogram")

    # Database
    # 默认存放在 data 目录下，方便 Docker 挂载整个目录
    DB_PATH = os.getenv("DB_PATH", "data/echogram.db")
    DB_URL = f"sqlite+aiosqlite:///{DB_PATH}"

    # Logging
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

    # Memory System [New]
    # 核心：短期记忆窗口大小 (Token)
    HISTORY_WINDOW_TOKENS = int(os.getenv("HISTORY_WINDOW_TOKENS", 6000))
    # 摘要：触发阈值 (Token)
    SUMMARY_TRIGGER_TOKENS = int(os.getenv("SUMMARY_TRIGGER_TOKENS", 2000))
    # 摘要：闲置阈值 (秒)
    SUMMARY_IDLE_SECONDS = int(os.getenv("SUMMARY_IDLE_SECONDS", 10800))
    # 摘要：使用的廉价模型
    SUMMARY_MODEL = os.getenv("SUMMARY_MODEL", "gpt-4o-mini")

    @classmethod
    def validate(cls):
        if not cls.TG_BOT_TOKEN:
            raise ValueError("TG_BOT_TOKEN is not set in .env")
        if not cls.ADMIN_USER_ID:
            raise ValueError("ADMIN_USER_ID is not set in .env")

settings = Settings()
