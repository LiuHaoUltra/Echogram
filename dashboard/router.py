from telegram.ext import CommandHandler, CallbackQueryHandler, ConversationHandler, MessageHandler, filters
from dashboard.handlers import dashboard_command, start_command, id_command
from dashboard.callbacks import menu_navigation_callback
from dashboard import input_handlers
from dashboard import wizard_handlers
from dashboard import model_handlers
from dashboard.states import *

def get_dashboard_handlers():
    """
    Dashboard 路由集合
    包含一个主 ConversationHandler 处理按键触发的输入状态
    """
    
    # 匹配所有可能的“进入输入模式”的按钮 callback_data regex
    entry_pattern = "^(set_api_url|set_api_key|set_model_name|set_sys_prompt|add_whitelist_id|remove_whitelist_id)$"

    conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(menu_navigation_callback, pattern=entry_pattern),
        ],
        states={
            WAITING_INPUT_API_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_handlers.save_api_url)],
            WAITING_INPUT_API_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_handlers.save_api_key)],
            # 模型选择: 支持文字输入，也支持回调 (翻页/选择)
            WAITING_INPUT_MODEL_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, input_handlers.save_model_name),
                CallbackQueryHandler(model_handlers.handle_model_callback)
            ],
            # 模型搜索
            WAITING_INPUT_MODEL_SEARCH: [
                 MessageHandler(filters.TEXT & ~filters.COMMAND, model_handlers.perform_model_search),
                 CallbackQueryHandler(model_handlers.handle_model_callback) # Allow cancel/back
            ],
            WAITING_INPUT_SYSTEM_PROMPT: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_handlers.save_system_prompt)],
            WAITING_INPUT_WHITELIST_ADD: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_handlers.add_whitelist_id)],
            WAITING_INPUT_WHITELIST_REMOVE: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_handlers.remove_whitelist_id)],
        },
        fallbacks=[
            CommandHandler("dashboard", dashboard_command),
            CallbackQueryHandler(menu_navigation_callback, pattern="^cancel_input$")
        ],
        allow_reentry=True
    )

    # 这里的 Wizard 是独立的 Conversation
    wizard_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(wizard_handlers.start_wizard_entry, pattern="^start_setup_wizard$")],
        states={
            WIZARD_INPUT_URL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, wizard_handlers.wizard_save_url),
                CallbackQueryHandler(wizard_handlers.wizard_use_default_url, pattern="^use_default_url$"),
                CallbackQueryHandler(wizard_handlers.wizard_skip_url, pattern="^skip_url$")
            ],
            WIZARD_INPUT_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, wizard_handlers.wizard_save_key)],
            
            # Wizard 中的模型选择
            WIZARD_INPUT_MODEL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, wizard_handlers.wizard_save_model),
                CallbackQueryHandler(model_handlers.handle_model_callback)
            ],
            # Wizard 时区设置
            WIZARD_INPUT_TIMEZONE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, wizard_handlers.wizard_save_timezone),
                CallbackQueryHandler(wizard_handlers.wizard_use_shanghai, pattern="^tz_shanghai$"),
                CallbackQueryHandler(wizard_handlers.wizard_use_utc, pattern="^tz_utc$")
            ]
        },
        fallbacks=[CommandHandler("start", start_command)],
        allow_reentry=True
    )

    return [
        wizard_conv,
        conv_handler,
        CommandHandler("start", start_command),
        CommandHandler("dashboard", dashboard_command),
        CommandHandler("id", id_command),
        CallbackQueryHandler(menu_navigation_callback) # 处理其他非输入类的点击（如菜单跳转）
    ]
