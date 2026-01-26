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

    @classmethod
    def validate(cls):
        if not cls.TG_BOT_TOKEN:
            raise ValueError("TG_BOT_TOKEN is not set in .env")
        if not cls.ADMIN_USER_ID:
            raise ValueError("ADMIN_USER_ID is not set in .env")

settings = Settings()
