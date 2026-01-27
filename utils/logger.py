import logging
import os
import sys
from config.settings import settings

def setup_logger():
    """配置全局日志"""
    logger = logging.getLogger()
    logger.setLevel(settings.LOG_LEVEL)

    # 控制台 Handler
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    handler.setFormatter(formatter)
    
    # 文件 Handler (启动即清空)
    log_dir = "logs"
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    
    file_path = os.path.join(log_dir, "echogram.log")
    file_handler = logging.FileHandler(file_path, mode='w', encoding='utf-8')
    file_handler.setFormatter(formatter)
    
    # 避免重复添加 handler
    if not logger.handlers:
        logger.addHandler(handler)
        logger.addHandler(file_handler)
        
    # 抑制第三方日志
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)

    return logging.getLogger("echogram")

logger = setup_logger()
