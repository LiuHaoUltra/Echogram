from telegram.ext import ApplicationBuilder, Application, CommandHandler
from config.settings import settings
from config.database import init_db
from utils.logger import logger

async def post_init(application: Application):
    """
    Bot 启动后的初始化操作
    1. 初始化数据库
    2. 设置 Bot 指令菜单 (后续添加)
    """
    logger.info("Initializing database...")
    await init_db()
    logger.info("Database initialized successfully.")
    
    # 获取 Bot 信息确认连接成功
    bot_info = await application.bot.get_me()
    logger.info(f"Bot connected: @{bot_info.username} (ID: {bot_info.id})")

def run_bot():
    """构造并运行 Bot"""
    try:
        settings.validate()
    except ValueError as e:
        logger.error(f"Configuration Error: {e}")
        return

    if not settings.TG_BOT_TOKEN:
        logger.error("TG_BOT_TOKEN not found! Please check your .env file.")
        return

    logger.info("Building application...")
    application = ApplicationBuilder().token(settings.TG_BOT_TOKEN)\
        .post_init(post_init)\
        .build()

    # ---------------------------------------------------------
    # 注册 Handlers
    # ---------------------------------------------------------
    # 1. Dashboard Handlers (Priority: High)
    from dashboard.router import get_dashboard_handlers
    application.add_handlers(get_dashboard_handlers())
    
    # 1.5 Admin Handlers
    from core.admin_handlers import reset_command, stats_command
    application.add_handler(CommandHandler("reset", reset_command))
    application.add_handler(CommandHandler("stats", stats_command))
    
    # 2. Chat Engine Handlers (Priority: Low, catch-all)
    from telegram.ext import MessageHandler, filters
    from core.chat_engine import process_message_entry
    
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_message_entry))
    
    # 3. Reaction Handlers
    from telegram.ext import MessageReactionHandler
    from core.chat_engine import process_reaction_update
    application.add_handler(MessageReactionHandler(process_reaction_update))
    
    # ---------------------------------------------------------

    logger.info("Starting polling...")
    # 显式允许 message_reaction 更新
    # Update.ALL_TYPES sometimes doesn't work as expected for new updates
    application.run_polling(allowed_updates=[
        "message", "edited_message", "channel_post", "edited_channel_post",
        "inline_query", "chosen_inline_result", "callback_query",
        "shipping_query", "pre_checkout_query", "poll", "poll_answer",
        "my_chat_member", "chat_member", "chat_join_request",
        "message_reaction", "message_reaction_count"
    ])
