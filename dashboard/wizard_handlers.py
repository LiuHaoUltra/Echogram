from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, ConversationHandler
from core.config_service import config_service
from dashboard.states import WIZARD_INPUT_URL, WIZARD_INPUT_KEY, WIZARD_INPUT_MODEL, WIZARD_INPUT_TIMEZONE, WIZARD_INPUT_SUMMARY_MODEL
from dashboard.keyboards import get_main_menu_keyboard

# --- Keyboards ---
def get_wizard_url_keyboard():
    keyboard = [
        [InlineKeyboardButton("使用默认 (OpenRouter)", callback_data="use_default_url")],
        [InlineKeyboardButton("跳过", callback_data="skip_url")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_wizard_skip_keyboard():
    keyboard = [[InlineKeyboardButton("跳过", callback_data="skip_step")]]
    return InlineKeyboardMarkup(keyboard)

def get_timezone_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇨🇳 使用北京时间 (Asia/Shanghai)", callback_data="tz_shanghai")],
        [InlineKeyboardButton("🌐 使用 UTC", callback_data="tz_utc")]
    ])

# --- Handlers ---

# 1. Step 1: Timezone (Entry)
async def start_wizard_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """进入向导：第一步设置时区"""
    """进入向导：第一步设置时区"""
    query = update.callback_query
    
    # 鉴权
    from core.secure import is_admin
    if not is_admin(update.effective_user.id):
        await query.answer("Access Denied", show_alert=True)
        return ConversationHandler.END

    await query.answer()
    
    msg = (
        "<b>🚀系统初始化向导 (1/4)</b>\n\n"
        "首先，请设置您的 **时区** (用于显示正确的时间)。\n"
        "推荐: <code>Asia/Shanghai</code>\n"
        "您可以直接点击下方按钮使用北京时间，或手动输入 (如 `Europe/London`)。"
    )
    await query.edit_message_text(text=msg, reply_markup=get_timezone_keyboard(), parse_mode="HTML")
    return WIZARD_INPUT_TIMEZONE

async def wizard_save_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """保存时区并进入下一步 (URL)"""
    text = update.message.text.strip()
    import pytz
    if text not in pytz.all_timezones:
        await update.message.reply_text("❌ 无效的时区名称。请重新输入 (例如 `Asia/Shanghai`) 或点击按钮。")
        return WIZARD_INPUT_TIMEZONE
        
    await config_service.set_value("timezone", text)
    await update.message.reply_text(f"✅ 已设置时区: {text}")
    return await _ask_url(update, context)

async def wizard_use_shanghai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await config_service.set_value("timezone", "Asia/Shanghai")
    await query.edit_message_text("✅ 已设置时区: Asia/Shanghai")
    return await _ask_url(update, context)

async def wizard_use_utc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await config_service.set_value("timezone", "UTC")
    await query.edit_message_text("✅ 已设置时区: UTC")
    return await _ask_url(update, context)

# 2. Step 2: URL
async def _ask_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "<b>🚀系统初始化向导 (2/4)</b>\n\n"
        "接下来，配置 LLM API 的接口地址。\n"
        "如果你使用 OpenRouter，请直接点击“使用默认”。\n\n"
        "请输入 <b>Base URL</b>:"
    )
    
    effective_message = update.effective_message
    if update.callback_query:
        await effective_message.reply_text(msg, reply_markup=get_wizard_url_keyboard(), parse_mode="HTML")
    else:
        await effective_message.reply_text(msg, reply_markup=get_wizard_url_keyboard(), parse_mode="HTML")
        
    return WIZARD_INPUT_URL

async def wizard_save_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    await config_service.set_value("api_base_url", text)
    await update.message.reply_text("✅ Base URL 已保存。")
    return await _ask_api_key(update, context)

async def wizard_use_default_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    default_url = "https://openrouter.ai/api/v1"
    await config_service.set_value("api_base_url", default_url)
    await query.edit_message_text(f"✅ 已使用默认 URL: {default_url}")
    return await _ask_api_key(update, context)

async def wizard_skip_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("⏭️ 已跳过 URL 配置。")
    return await _ask_api_key(update, context)

# 3. Step 3: API Key
async def _ask_api_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "<b>🚀系统初始化向导 (3/4)</b>\n\n"
        "请输入你的 <b>API Key</b>。\n"
        "<i>(输入后消息将立即销毁消息以保护隐私)</i>"
    )
    
    effective_message = update.effective_message
    if update.callback_query:
        await effective_message.reply_text(msg, parse_mode="HTML")
    else:
        await effective_message.reply_text(msg, parse_mode="HTML")
        
    return WIZARD_INPUT_KEY

async def wizard_save_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        await update.message.delete()
    except:
        pass
    
    await config_service.set_value("api_key", text)
    await update.message.reply_text("✅ API Key 已保存。")
    
    return await _ask_model(update, context)

# 4. Step 4: Model
from dashboard.model_handlers import show_model_selection_panel

async def _ask_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "<b>🚀系统初始化向导 (4/4)</b>\n\n"
        "最后，请选择或输入要使用的模型名称 (Model Name)。"
    )
    # 提示用户，并调用面板
    # 由于 show_model_selection_panel 是独立的，我们在这里通过消息告诉用户可以操作了
    # 但为了更好的体验，我们直接调用 show_panel
    await update.message.reply_text(msg, parse_mode="HTML")
    await show_model_selection_panel(update, context)
    return WIZARD_INPUT_MODEL

async def wizard_save_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    await config_service.set_value("model_name", text)
    
    return await _ask_summary_model(update, context)

# 5. Step 5: Summary Model (New)
async def _ask_summary_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "<b>🚀系统初始化向导 (5/5)</b>\n\n"
        "配置 **长期记忆摘要模型**。\n"
        "建议使用更便宜、速度更快的模型 (如 `gpt-4o-mini`) 来处理后台摘要任务，以节省成本。\n"
        "如果不设置，将默认使用主模型。"
    )
    # 添加一个明显的跳过按钮
    keyboard = [[InlineKeyboardButton("⏭️ 使用主模型 (默认)", callback_data="skip_summary_model")]]
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    
    # 使用面板，设置 target='summary'
    # 注意：wizard 状态机需要能够处理从面板返回的回调
    # 我们这里直接调用面板，用户点击后会触发 handle_model_callback
    # handle_model_callback 会结束 ConversationHandler.END ??? 
    # 不，handle_model_callback 返回 END。这在 Wizard 中是个问题。
    # Wizard 是个 ConversationHandler。
    # 如果 handle_model_callback 返回 END，整个 Wizard 就结束了，_finalize_wizard 就没机会跑了。
    
    # 解决方案：
    # Wizard 中的 handle_model_callback 需要特殊处理吗？
    # 或者我们在 router 中，把 Wizard 的 WIZARD_INPUT_SUMMARY_MODEL 状态下的 callback 指向一个特殊的 handler？
    # 是的，我们需要一个 wizard_handle_summary_model_callback。
    
    # 但 model_handlers.handle_model_callback 逻辑比较复杂（翻页等）。复制一份太冗余。
    # 我们可以复用逻辑，但返回值需要控制。
    
    # 鉴于 Wizard 流程的线性，我们不妨让这一步简单点：
    # 提供两个按钮："使用主模型(跳过)" 和 "手动输入/选择"？
    # 为了复用面板，我们可以在 wizard_handlers 里写一个 wrapper。
    
    await show_model_selection_panel(update, context, target="summary")
    return WIZARD_INPUT_SUMMARY_MODEL

async def wizard_save_summary_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 手动输入的情况
    text = update.message.text.strip()
    if text.lower() not in ["skip", "跳过"]:
        await config_service.set_value("summary_model_name", text)
    
    return await _finalize_wizard(update, context)

async def wizard_skip_summary_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    # 设为空，即跟随主模型
    await config_service.set_value("summary_model_name", "")
    return await _finalize_wizard(update, context)

from dashboard.model_handlers import handle_model_callback

async def wizard_model_callback_wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Wrapper to handle model selection within Wizard.
    If handle_model_callback returns END, we proceed to finalize wizard.
    Otherwise we stay in WIZARD_INPUT_SUMMARY_MODEL.
    """
    res = await handle_model_callback(update, context)
    if res == ConversationHandler.END:
        return await _finalize_wizard(update, context)
    return WIZARD_INPUT_SUMMARY_MODEL

# Finalize
async def _finalize_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    effective_message = update.callback_query.message if update.callback_query else update.message
    
    text = (
        "<b>🎉 初始化完成！</b>\n\n"
        "Echogram 核心已启动。你现在可以开始对话，或打开控制面板进行微调。"
    )
    
    chat_id = effective_message.chat_id
    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=get_main_menu_keyboard(),
        parse_mode="HTML"
    )
    return ConversationHandler.END
