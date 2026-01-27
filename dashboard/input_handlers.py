from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler
from core.config_service import config_service
from core.access_service import access_service
from dashboard.keyboards import get_main_menu_keyboard, get_persona_keyboard, get_access_control_keyboard, get_api_settings_keyboard, get_memory_keyboard, get_cancel_keyboard
from dashboard.states import WAITING_INPUT_API_URL, WAITING_INPUT_API_KEY, WAITING_INPUT_MODEL_NAME, WAITING_INPUT_SYSTEM_PROMPT
from dashboard.handlers import get_dashboard_overview_text

async def _try_delete_previous_panel(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """尝试删除上一个面板消息 (如果存在)"""
    last_id = context.user_data.pop('last_panel_id', None)
    if last_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=last_id)
        except:
            pass

# --- API 设置 ---
async def save_api_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _try_delete_previous_panel(context, update.effective_chat.id)
    text = update.message.text.strip()
    if not text.startswith("http"):
        await update.message.reply_text("❌ 无效的 URL。必须以 `http` 或 `https` 开头。", reply_markup=get_cancel_keyboard())
        return WAITING_INPUT_API_URL
        
    await config_service.set_value("api_base_url", text)
    await config_service.set_value("api_base_url", text)
    await update.message.reply_text(f"✅ Base URL 已更新为: {text}")
    
    # 刷新面板
    overview = await get_dashboard_overview_text(update.effective_chat.id)
    await update.message.reply_text(overview, reply_markup=get_main_menu_keyboard(), parse_mode="HTML")
    return ConversationHandler.END

async def save_api_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _try_delete_previous_panel(context, update.effective_chat.id)
    text = update.message.text.strip()
    if len(text) < 8:
        await update.message.reply_text("❌ API Key 太短，请检查。", reply_markup=get_cancel_keyboard())
        return WAITING_INPUT_API_KEY

    try:
        await update.message.delete()
    except:
        pass
    await config_service.set_value("api_key", text)
    await config_service.set_value("api_key", text)
    await update.message.reply_text(f"✅ API Key 已更新。")
    
    overview = await get_dashboard_overview_text(update.effective_chat.id)
    await update.message.reply_text(overview, reply_markup=get_main_menu_keyboard(), parse_mode="HTML")
    return ConversationHandler.END

async def save_model_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _try_delete_previous_panel(context, update.effective_chat.id)
    text = update.message.text.strip()
    if len(text) < 2:
         await update.message.reply_text("❌ 模型名称太短。", reply_markup=get_cancel_keyboard())
         return WAITING_INPUT_MODEL_NAME

    await config_service.set_value("model_name", text)
    await config_service.set_value("model_name", text)
    await update.message.reply_text(f"✅ Model Name 已更新为: {text}")
    
    overview = await get_dashboard_overview_text(update.effective_chat.id)
    await update.message.reply_text(overview, reply_markup=get_main_menu_keyboard(), parse_mode="HTML")
    return ConversationHandler.END

async def save_aggregation_latency(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _try_delete_previous_panel(context, update.effective_chat.id)
    text = update.message.text.strip()
    try:
        val = float(text)
        if val < 0.1 or val > 60:
            await update.message.reply_text(f"❌ 范围错误，请输入 0.1 ~ 60 之间的数字。", reply_markup=get_api_settings_keyboard())
            return ConversationHandler.END
            
        await config_service.set_value("aggregation_latency", str(val))
        await config_service.set_value("aggregation_latency", str(val))
        await update.message.reply_text(f"✅ 聚合延迟已更新为: {val} 秒")
        
        overview = await get_dashboard_overview_text(update.effective_chat.id)
        await update.message.reply_text(overview, reply_markup=get_main_menu_keyboard(), parse_mode="HTML")
    except ValueError:
        await update.message.reply_text(f"❌ 输入无效，必须是数字。", reply_markup=get_api_settings_keyboard())
    return ConversationHandler.END

# --- 人格设置 ---
async def save_system_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _try_delete_previous_panel(context, update.effective_chat.id)
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("❌ 内容不能为空。", reply_markup=get_cancel_keyboard())
        return WAITING_INPUT_SYSTEM_PROMPT

    await config_service.set_value("system_prompt", text)
    await config_service.set_value("system_prompt", text)
    await update.message.reply_text(f"✅ System Prompt 已更新。")
    
    overview = await get_dashboard_overview_text(update.effective_chat.id)
    await update.message.reply_text(overview, reply_markup=get_main_menu_keyboard(), parse_mode="HTML")
    return ConversationHandler.END

# --- 访问控制 ---
async def add_whitelist_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _try_delete_previous_panel(context, update.effective_chat.id)
    text = update.message.text.strip()
    try:
        chat_id = int(text)
        await access_service.add_whitelist(chat_id=chat_id, type_="manual")
        await access_service.add_whitelist(chat_id=chat_id, type_="manual")
        await update.message.reply_text(f"✅ ID {chat_id} 已添加到白名单。")
        
        overview = await get_dashboard_overview_text(update.effective_chat.id)
        await update.message.reply_text(overview, reply_markup=get_main_menu_keyboard(), parse_mode="HTML")
    except ValueError:
        await update.message.reply_text(f"❌ 无效的 ID，必须是数字。", reply_markup=get_access_control_keyboard())
    return ConversationHandler.END

async def remove_whitelist_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _try_delete_previous_panel(context, update.effective_chat.id)
    text = update.message.text.strip()
    try:
        chat_id = int(text)
        await access_service.remove_whitelist(chat_id=chat_id)
        await access_service.remove_whitelist(chat_id=chat_id)
        await update.message.reply_text(f"✅ ID {chat_id} 已从白名单移除。")
        
        overview = await get_dashboard_overview_text(update.effective_chat.id)
        await update.message.reply_text(overview, reply_markup=get_main_menu_keyboard(), parse_mode="HTML")
    except ValueError:
        await update.message.reply_text(f"❌ 无效的 ID。", reply_markup=get_access_control_keyboard())
    return ConversationHandler.END

# --- 记忆设置 ---
async def save_summary_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _try_delete_previous_panel(context, update.effective_chat.id)
    text = update.message.text.strip()
    
    if text.lower() in ["default", "reset"]:
        await config_service.set_value("summary_model_name", "")
        await update.message.reply_text("✅ 已重置摘要模型（将跟随主模型）。")
    else:
        await config_service.set_value("summary_model_name", text)
        await update.message.reply_text(f"✅ 摘要模型已设置为: {text}")

    overview = await get_dashboard_overview_text(update.effective_chat.id)
    await update.message.reply_text(overview, reply_markup=get_main_menu_keyboard(), parse_mode="HTML")
    return ConversationHandler.END



async def save_history_tokens(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _try_delete_previous_panel(context, update.effective_chat.id)
    text = update.message.text.strip()
    try:
        val = int(text)
        if val < 300 or val > 100000:
             await update.message.reply_text(f"❌ 范围错误，请输入 300 ~ 100000 之间的整数。", reply_markup=get_memory_keyboard())
             return ConversationHandler.END
        
        await config_service.set_value("history_tokens", str(val))
        await config_service.set_value("history_tokens", str(val))
        await update.message.reply_text(f"✅ 历史记录 Token 上限已更新为: {val}")
        
        overview = await get_dashboard_overview_text(update.effective_chat.id)
        await update.message.reply_text(overview, reply_markup=get_main_menu_keyboard(), parse_mode="HTML")
    except ValueError:
        await update.message.reply_text(f"❌ 输入无效，必须是整数。", reply_markup=get_memory_keyboard())
    return ConversationHandler.END
