from config.settings import settings

def is_admin(user_id: int) -> bool:
    """检查用户是否为超级管理员"""
    return user_id == settings.ADMIN_USER_ID
