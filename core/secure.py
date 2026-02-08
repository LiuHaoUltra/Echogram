from config.settings import settings

from telegram import Update, constants
from telegram.ext import ContextTypes
import functools
from utils.logger import logger

def is_admin(user_id: int) -> bool:
    """检查超级管理员"""
    return user_id == settings.ADMIN_USER_ID

def require_admin_access(func):
    """
    通用鉴权装饰器：
    1. 必须是 Admin User
    2. 如果在群组中，必须在 Whitelist 中
    """
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        chat = update.effective_chat
        
        # 1. User Valid
        if not user or not is_admin(user.id):
            # Silent Fail for non-admins to avoid spam
            return

        # 2. Group Valid
        if chat.type in (constants.ChatType.GROUP, constants.ChatType.SUPERGROUP):
            # Lazy Import to avoid circular dependency
            from core.access_service import access_service
            if not await access_service.is_whitelisted(chat.id):
                # Silent Fail for non-whitelisted groups
                return
        
        return await func(update, context, *args, **kwargs)
    return wrapper
