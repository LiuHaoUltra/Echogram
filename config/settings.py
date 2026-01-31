"""全局配置：环境变量加载与默认值设置"""

import os
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()

class Settings:
    # Telegram Bot 配置
    TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
    ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", 0))
    BOT_NAME = os.getenv("BOT_NAME", "Echogram")

    # 数据库配置
    DB_PATH = os.getenv("DB_PATH", "data/echogram.db")
    DB_URL = f"sqlite+aiosqlite:///{DB_PATH}"

    # 日志配置
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

    # 记忆系统配置
    HISTORY_WINDOW_TOKENS = int(os.getenv("HISTORY_WINDOW_TOKENS", 6000))  # 历史窗口大小
    SUMMARY_TRIGGER_TOKENS = int(os.getenv("SUMMARY_TRIGGER_TOKENS", 2000))  # 摘要触发阈值
    SUMMARY_IDLE_SECONDS = int(os.getenv("SUMMARY_IDLE_SECONDS", 10800))  # 闲置触发时间
    
    # OpenAI API 配置
    OPENAI_API_BASE = os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL_NAME = os.getenv("OPENAI_MODEL_NAME", "gpt-4o-mini")
    SUMMARY_MODEL = os.getenv("SUMMARY_MODEL", "gpt-4o-mini")
    
    @classmethod
    def validate(cls):
        """验证必需配置项"""
        if not cls.TG_BOT_TOKEN:
            raise ValueError("TG_BOT_TOKEN is not set in .env")
        if not cls.ADMIN_USER_ID:
            raise ValueError("ADMIN_USER_ID is not set in .env")

settings = Settings()
