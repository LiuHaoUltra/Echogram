from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler
from core.config_service import config_service
from core.access_service import access_service
from dashboard.keyboards import get_main_menu_keyboard, get_persona_keyboard, get_access_control_keyboard

# --- API Settings ---
async def save_api_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    await config_service.set_value("api_base_url", text)
    await update.message.reply_text(f"✅ Base URL 已更新为: {text}", reply_markup=get_main_menu_keyboard())
    return ConversationHandler.END

async def save_api_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        await update.message.delete()
    except:
        pass
    await config_service.set_value("api_key", text)
    await update.message.reply_text(f"✅ API Key 已更新。", reply_markup=get_main_menu_keyboard())
    return ConversationHandler.END

async def save_model_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    await config_service.set_value("model_name", text)
    await update.message.reply_text(f"✅ Model Name 已更新为: {text}", reply_markup=get_main_menu_keyboard())
    return ConversationHandler.END

# --- Persona Settings ---
async def save_system_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    await config_service.set_value("system_prompt", text)
    await update.message.reply_text(f"✅ System Prompt 已更新。", reply_markup=get_persona_keyboard())
    return ConversationHandler.END

# --- Access Control ---
async def add_whitelist_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        chat_id = int(text)
        await access_service.add_whitelist(chat_id=chat_id, type_="manual")
        await update.message.reply_text(f"✅ ID {chat_id} 已添加到白名单。", reply_markup=get_access_control_keyboard())
    except ValueError:
        await update.message.reply_text(f"❌ 无效的 ID，必须是数字。", reply_markup=get_access_control_keyboard())
    return ConversationHandler.END

async def remove_whitelist_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        chat_id = int(text)
        await access_service.remove_whitelist(chat_id=chat_id)
        await update.message.reply_text(f"✅ ID {chat_id} 已从白名单移除。", reply_markup=get_access_control_keyboard())
    except ValueError:
        await update.message.reply_text(f"❌ 无效的 ID。", reply_markup=get_access_control_keyboard())
    return ConversationHandler.END
