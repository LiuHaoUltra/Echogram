import logging
import sys
from config.settings import settings

def setup_logger():
    """配置全局日志格式与级别"""
    logger = logging.getLogger()
    logger.setLevel(settings.LOG_LEVEL)

    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    handler.setFormatter(formatter)
    
    # 避免重复添加 handler
    if not logger.handlers:
        logger.addHandler(handler)
        
    # 调整第三方库的日志级别以减少噪音
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)

    return logging.getLogger("echogram")

logger = setup_logger()
