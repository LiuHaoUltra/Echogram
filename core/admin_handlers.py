from telegram import Update
from telegram.ext import ContextTypes
from core.history_service import history_service
from core.secure import is_admin

async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /reset 指令：清空当前对话的历史记忆
    用于修复上下文污染
    """
    user = update.effective_user
    chat = update.effective_chat
    
    # 鉴权：仅 Admin 或 私聊
    # 其实群组里任何成员如果能用 bot 应该也能 reset? 先限制 admin
    if not is_admin(user.id):
        # 除非是私聊，私聊允许自己 reset
        if chat.type != 'private':
            return

    await history_service.clear_history(chat.id)
    await update.message.reply_text("🧹 记忆已重置！上下文已清空。")
