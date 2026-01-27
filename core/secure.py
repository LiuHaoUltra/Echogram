from config.settings import settings

def is_admin(user_id: int) -> bool:
    """检查超级管理员"""
    return user_id == settings.ADMIN_USER_ID
