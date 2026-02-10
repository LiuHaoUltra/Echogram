"""日志配置：控制台与文件双输出"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from config.settings import settings

def setup_logger():
    """配置全局日志器"""
    logger = logging.getLogger()
    logger.setLevel(settings.LOG_LEVEL)

    # 控制台输出
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    handler.setFormatter(formatter)
    
    # 文件输出（按大小轮转，避免日志无限膨胀）
    log_dir = "logs"
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    
    file_path = os.path.join(log_dir, "echogram.log")
    file_handler = RotatingFileHandler(
        file_path,
        maxBytes=settings.LOG_MAX_BYTES,
        backupCount=settings.LOG_BACKUP_COUNT,
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    
    # 避免重复添加
    if not logger.handlers:
        logger.addHandler(handler)
        logger.addHandler(file_handler)
        
    # 降低第三方库日志级别
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)

    return logging.getLogger("echogram")

logger = setup_logger()
