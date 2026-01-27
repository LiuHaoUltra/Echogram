from telegram import Update, constants, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from core.secure import is_admin
from core.config_service import config_service
from config.settings import settings
from dashboard.keyboards import get_main_menu_keyboard

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    处理 /start 命令
    """
    user = update.effective_user
    chat = update.effective_chat

    # 1. 鉴权 & 私聊检查
    if chat.type != constants.ChatType.PRIVATE:
        if is_admin(user.id):
             await update.message.reply_text("👋 管理员你好。请私聊我进行配置。")
        return

    if not is_admin(user.id):
        # 陌生人私聊 /start -> 静默
        return
        
    # 检查初始化
    api_key = await config_service.get_value("api_key")
    
    if not api_key:
        keyboard = [[InlineKeyboardButton("🚀 开始初始化配置", callback_data="start_setup_wizard")]]
        await update.message.reply_text(
            f"👋 <b>欢迎回来，管理员 {user.first_name}！</b>\n\n"
            "⚠️ 检测到核心配置缺失 (API Key)。\n"
            "请点击下方按钮启动配置向导：",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=constants.ParseMode.HTML
        )
    else:
        await update.message.reply_text(
            f"👋 <b>欢迎回来，管理员 {user.first_name}！</b>\n\n"
            "系统核心已就绪。请点击下方按钮或发送 /dashboard 打开控制台。",
            reply_markup=get_main_menu_keyboard(),
            parse_mode=constants.ParseMode.HTML
        )

# dashboard_command 保持不变...
async def get_dashboard_overview_text(chat_id: int = 0) -> str:
    """获取 Dashboard 总览文本"""
    configs = await config_service.get_all_settings()
    
    base_url = configs.get("api_base_url", "未设置")
    if len(base_url) > 50: base_url = base_url[:47] + "..."
        
    model = configs.get("model_name", "gpt-3.5-turbo")
    if len(model) > 30: model = model[:27] + "..."

    summary_model = configs.get("summary_model_name")
    if not summary_model:
        summary_model_disp = "<i>(Same as Main)</i>"
    else:
        if len(summary_model) > 30: summary_model = summary_model[:27] + "..."
        summary_model_disp = f"<code>{summary_model}</code>"

    latency = configs.get("aggregation_latency", "10.0")

    return (
        "<b>Echogram 控制中心</b>\n\n"
        "📊 <b>系统参数</b>\n"
        f"• Base URL: <code>{base_url}</code>\n"
        f"• Main Model: <code>{model}</code>\n"
        f"• Summary Model: {summary_model_disp}\n"
        f"• Aggregation Latency: <code>{latency} s</code>\n"
        f"• Memory & Archiving Threshold (T): <code>{configs.get('history_tokens', str(settings.HISTORY_WINDOW_TOKENS))} tokens</code>\n\n"
        "请选择配置项："
    )

async def dashboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    if not is_admin(user.id): return # Silence
    
    overview_text = await get_dashboard_overview_text(user.id)
    
    if chat.type != constants.ChatType.PRIVATE:
        try: await update.message.delete()
        except: pass
        temp_msg = await context.bot.send_message(chat.id, f"👋 嗨 {user.first_name}，控制面板已发送至私聊。", disable_notification=True)
        try:
            await context.bot.send_message(user.id, overview_text, reply_markup=get_main_menu_keyboard(), parse_mode="HTML")
            context.job_queue.run_once(lambda ctx: ctx.bot.delete_message(chat.id, temp_msg.message_id), when=5)
        except: await context.bot.send_message(chat.id, "❌ 无法发送私信。请先私聊我发送 /start 以开启权限。")
        return
    await update.message.reply_text(overview_text, reply_markup=get_main_menu_keyboard(), parse_mode="HTML")

async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    获取当前 Chat ID (方便添加白名单)
    """
    user = update.effective_user
    chat = update.effective_chat
    
    # 鉴权：非管理员完全静默
    if not is_admin(user.id):
        return
        
    await update.message.reply_text(
        f"🆔 <b>Current Chat ID:</b> <code>{chat.id}</code>\n"
        f"Type: {chat.type}",
        parse_mode=constants.ParseMode.HTML
    )
