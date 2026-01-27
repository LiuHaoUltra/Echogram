from telegram import Update
from telegram.ext import ContextTypes
from core.history_service import history_service
from core.secure import is_admin

async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /reset 指令：清空当前对话的历史记忆
    """
    user = update.effective_user
    chat = update.effective_chat
    
    # 鉴权：仅 Admin
    if not is_admin(user.id):
        return

    # [NEW] 仅允许在群组中使用 (私聊已无对话数据)
    if chat.type == 'private':
        # Admin is known here
        await update.message.reply_text("⚠️ 私聊不产生记忆，无需重置。请在群组中使用此指令。")
        return

    await history_service.clear_history(chat.id)
    # [NEW] 同时清空长期摘要
    from core.summary_service import summary_service
    await summary_service.clear_summary(chat.id)
    
    await update.message.reply_text("🧹 记忆已重置！上下文和长期摘要均已清空。")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /stats 指令：查看当前会话的记忆状态
    """
    user = update.effective_user
    chat = update.effective_chat
    
    if not is_admin(user.id):
        return
        
    if chat.type == 'private':
        await update.message.reply_text("⚠️ 私聊不产生记忆。")
        return

    # 1.获取配置
    from core.config_service import config_service
    from config.settings import settings
    
    limit_str = await config_service.get_value("history_tokens")
    token_limit = int(limit_str) if limit_str and limit_str.isdigit() else settings.HISTORY_WINDOW_TOKENS
    
    # 2.获取实时数据
    from core.history_service import history_service
    from core.memory_service import memory_service
    
    current_tokens = await history_service.calculate_context_usage(chat.id, token_limit)
    last_summary_time = await memory_service.get_latest_summary_time(chat.id)
    
    # 3.计算百分比
    usage_percent = round((current_tokens / token_limit) * 100, 1)
    bar_len = 10
    filled_len = int(bar_len * (current_tokens / token_limit))
    # Cap at 100% vis
    if filled_len > bar_len: filled_len = bar_len
    progress_bar = "█" * filled_len + "░" * (bar_len - filled_len)

    msg = (
        f"📊 <b>Session Statistics</b>\n\n"
        f"🆔 Chat ID: <code>{chat.id}</code>\n"
        f"🧠 Memory Usage:\n"
        f"<code>{progress_bar} {usage_percent}%</code>\n"
        f"({current_tokens} / {token_limit} tokens)\n\n"
        f"🕒 Last Summary: {last_summary_time}"
    )
    
    await update.message.reply_text(msg, parse_mode='HTML')
