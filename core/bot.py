from telegram.ext import ApplicationBuilder, Application, CommandHandler
from config.settings import settings
from config.database import init_db
from utils.logger import logger
# 引入模型以确保建表
import models 
from core.news_push_service import news_push_service

# 全局 Bot 实例，供服务层（如 RAG）在无 Context 场景下使用
bot = None

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
    
    # --- Schema Patch (Fix for message_type missing) ---
    try:
        from config.database import get_db_session
        from sqlalchemy import text
        async for session in get_db_session():
            try:
                # 检查列是否存在 (SQLite)
                await session.execute(text("SELECT message_type FROM history LIMIT 1"))
            except Exception:
                logger.warning("Column 'message_type' missing in 'history'. Applying patch...")
                # SQLite 不支持 IF NOT EXISTS COLUMN，捕获异常即代表需要添加
                try:
                    await session.execute(text("ALTER TABLE history ADD COLUMN message_type VARCHAR(10) DEFAULT 'text'"))
                    await session.commit()
                    logger.info("Schema patch applied: 'message_type' column added.")
                except Exception as e:
                    logger.error(f"Failed to apply schema patch: {e}")
            break # 用完即弃
    except Exception as e:
        logger.error(f"Schema check failed: {e}")

    # --- Schema Patch (Fix for file_id missing) ---
    try:
        from config.database import get_db_session
        from sqlalchemy import text
        async for session in get_db_session():
            try:
                # 检查列是否存在 (SQLite)
                await session.execute(text("SELECT file_id FROM history LIMIT 1"))
            except Exception:
                logger.warning("Column 'file_id' missing in 'history'. Applying patch...")
                try:
                    await session.execute(text("ALTER TABLE history ADD COLUMN file_id VARCHAR(255)"))
                    await session.commit()
                    logger.info("Schema patch applied: 'file_id' column added.")
                except Exception as e:
                    logger.error(f"Failed to apply schema patch (file_id): {e}")
            break # 用完即弃
    except Exception as e:
        logger.error(f"Schema check (file_id) failed: {e}")

    # --- Schema Patch (Fix for denoised_content missing in RAG v2) ---
    try:
        from config.database import get_db_session
        from sqlalchemy import text
        async for session in get_db_session():
            try:
                # Check if column exists
                await session.execute(text("SELECT denoised_content FROM rag_status LIMIT 1"))
            except Exception:
                logger.warning("Column 'denoised_content' missing in 'rag_status'. Applying patch...")
                try:
                    await session.execute(text("ALTER TABLE rag_status ADD COLUMN denoised_content TEXT"))
                    await session.commit()
                    logger.info("Schema patch applied: 'denoised_content' column added.")
                except Exception as e:
                    logger.error(f"Failed to apply schema patch (denoised_content): {e}")
            break
    except Exception as e:
        logger.error(f"Schema check (denoised_content) failed: {e}")
    # ---------------------------------------------------
    # ---------------------------------------------------
    
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

        # 2. RAG Background Sync (Every 5 min)
        # Wrapper to match JobQueue signature
        async def rag_sync_wrapper(context):
            from core.rag_service import rag_service
            await rag_service.run_background_sync()

        application.job_queue.run_repeating(
            rag_sync_wrapper, 
            interval=300, 
            first=30, 
            name="rag_sync_loop"
        )
        logger.info("RAG Sync: Scheduler registered (Interval: 300s)")
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

    # ---------------------------------------------------------
    # ---------------------------------------------------------
    # 注册处理器
    # ---------------------------------------------------------
    # Dashboard 处理器 (高优先级)
    from dashboard.router import get_dashboard_handlers
    application.add_handlers(get_dashboard_handlers())
    
    # Admin 处理器
    from core.admin_handlers import (
        reset_command, stats_command, prompt_command, 
        debug_command, add_whitelist_command, remove_whitelist_command,
        sub_command, push_now_command
    )
    application.add_handler(CommandHandler("reset", reset_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("prompt", prompt_command))
    application.add_handler(CommandHandler("debug", debug_command))
    application.add_handler(CommandHandler("add_whitelist", add_whitelist_command))
    application.add_handler(CommandHandler("remove_whitelist", remove_whitelist_command))
    application.add_handler(CommandHandler("sub", sub_command))
    application.add_handler(CommandHandler("sub", sub_command))
    application.add_handler(CommandHandler("push_now", push_now_command))
    
    # 聊天引擎处理器 (低优先级)
    from telegram.ext import MessageHandler, filters
    from core.chat_engine import process_message_entry, process_voice_message_entry, process_photo_entry
    
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_message_entry))
    application.add_handler(MessageHandler(filters.VOICE, process_voice_message_entry))  # 语音消息处理
    application.add_handler(MessageHandler(filters.PHOTO, process_photo_entry))  # 图片消息处理
    
    # 回应处理器
    from telegram.ext import MessageReactionHandler
    from core.chat_engine import process_reaction_update
    application.add_handler(MessageReactionHandler(process_reaction_update))
    
    # ---------------------------------------------------------

    logger.info("Starting polling...")
    # 允许回应更新
    # 显式指定类型以确保兼容性
    application.run_polling(
        allowed_updates=[
            "message", "edited_message", "channel_post", "edited_channel_post",
            "inline_query", "chosen_inline_result", "callback_query",
            "shipping_query", "pre_checkout_query", "poll", "poll_answer",
            "my_chat_member", "chat_member", "chat_join_request",
            "message_reaction", "message_reaction_count"
        ]
    )
