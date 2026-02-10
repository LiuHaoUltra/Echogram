from telegram.ext import ApplicationBuilder, Application, CommandHandler, ContextTypes
from telegram.error import NetworkError
from config.settings import settings
from config.database import init_db
from utils.logger import logger
# 引入模型以确保建表
import models 
from core.news_push_service import news_push_service

# 全局 Bot 实例，供服务层（如 RAG）在无 Context 场景下使用
bot = None


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    """全局异常处理：网络抖动降噪，其它异常保留堆栈"""
    err = context.error

    if isinstance(err, NetworkError):
        logger.warning(f"Telegram transient network error: {err}")
        return

    logger.error(f"Unhandled exception in Telegram handler: {err}", exc_info=err)

async def post_init(application: Application):
    """
    Bot 初始化
    1. 初始化数据库
    2. 注册定时任务 (NewsPush)
    """
    logger.info("Initializing database...")
    await init_db()
    
    global bot
    bot = application.bot
    
    logger.info("Database initialized successfully.")
    
    logger.info("Database initialized successfully.")
    
    # 确认连接
    bot_info = await application.bot.get_me()
    logger.info(f"Bot connected: @{bot_info.username} (ID: {bot_info.id})")

    # ---------------------------------------------------------
    # 注册 NewsPush Service 定时任务
    # ---------------------------------------------------------
    if application.job_queue:
        # 1. NewsPush (Every 1h)
        application.job_queue.run_repeating(
            news_push_service.run_push_loop, 
            interval=3600, 
            first=60, 
            name="news_push_loop"
        )
        logger.info("NewsPush: Scheduler registered (Interval: 3600s)")

        # 2. RAG Background Sync (Every 2 min)
        # Wrapper to match JobQueue signature
        async def rag_sync_wrapper(context):
            from core.rag_service import rag_service
            await rag_service.run_background_sync()

        application.job_queue.run_repeating(
            rag_sync_wrapper, 
            interval=120, 
            first=30, 
            name="rag_sync_loop"
        )
        logger.info("RAG Sync: Scheduler registered (Interval: 120s)")
    else:
        logger.warning("JobQueue not available! RAG & NewsPush will not auto-run.")

def run_bot():
    """启动 Bot"""
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

    # 全局错误处理（避免 No error handlers are registered）
    application.add_error_handler(on_error)

    # ---------------------------------------------------------
    # 注册处理器
    # ---------------------------------------------------------
    # 1. Admin Callbacks (最高优先级，防止被 Dashboard Catch-all 拦截)
    from core.admin_handlers import admin_action_callback, antenna_action_callback
    from telegram.ext import CallbackQueryHandler
    application.add_handler(CallbackQueryHandler(admin_action_callback, pattern="^admin:"))
    application.add_handler(CallbackQueryHandler(antenna_action_callback, pattern="^antenna:"))

    # 2. Dashboard 处理器 (包含 wizard, menu navigation 等)
    from dashboard.router import get_dashboard_handlers
    application.add_handlers(get_dashboard_handlers())
    
    # 3. Admin Commands
    from core.admin_handlers import (
        reset_command, stats_command, prompt_command, 
        debug_command, add_whitelist_command, remove_whitelist_command,
        sub_command, push_now_command, antenna_command,
        edit_command, delete_command
    )

    application.add_handler(CommandHandler("reset", reset_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("prompt", prompt_command))
    application.add_handler(CommandHandler("debug", debug_command))
    application.add_handler(CommandHandler("add_whitelist", add_whitelist_command))
    application.add_handler(CommandHandler("remove_whitelist", remove_whitelist_command))
    application.add_handler(CommandHandler("sub", sub_command))
    application.add_handler(CommandHandler("push_now", push_now_command))
    application.add_handler(CommandHandler("antenna", antenna_command))
    
    application.add_handler(CommandHandler("edit", edit_command))
    application.add_handler(CommandHandler(["del", "delete"], delete_command))
    
    # 聊天引擎处理器 (低优先级)
    from telegram.ext import MessageHandler, filters
    from core.chat_engine import process_message_entry, process_voice_message_entry, process_photo_entry, process_message_edit
    
    # /antenna URL 输入接管（仅当 antenna_pending 存在时会拦截并停止后续处理）
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, antenna_action_callback), group=0)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_message_entry))
    application.add_handler(MessageHandler(filters.VOICE, process_voice_message_entry))  # 语音消息处理
    application.add_handler(MessageHandler(filters.PHOTO, process_photo_entry))  # 图片消息处理
    application.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE, process_message_edit)) # 原生编辑监听
    
    # 回应处理器
    from telegram.ext import MessageReactionHandler
    from core.chat_engine import process_reaction_update
    application.add_handler(MessageReactionHandler(process_reaction_update))
    
    # ---------------------------------------------------------

    logger.info("Starting polling...")
    # 允许回应更新
    # 显式指定类型以确保兼容性
    application.run_polling(
        bootstrap_retries=-1,
        timeout=30,
        allowed_updates=[
            "message", "edited_message", "channel_post", "edited_channel_post",
            "inline_query", "chosen_inline_result", "callback_query",
            "shipping_query", "pre_checkout_query", "poll", "poll_answer",
            "my_chat_member", "chat_member", "chat_join_request",
            "message_reaction", "message_reaction_count"
        ]
    )
