"""语音配置输入处理"""

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from core.config_service import config_service
from dashboard.handlers import get_dashboard_overview_text
from dashboard.keyboards import get_main_menu_keyboard, get_voice_keyboard


async def handle_tts_url_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 TTS URL 输入"""
    url = update.message.text.strip()
    await config_service.set_value("tts_api_url", url)
    
    await update.message.reply_text(f"✅ TTS API URL 已设置为: {url}")
    
    # 返回主菜单
    overview = await get_dashboard_overview_text(update.effective_chat.id)
    panel_id = context.user_data.get('last_panel_id')
    
    if panel_id:
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=panel_id,
            text=overview,
            reply_markup=get_main_menu_keyboard(),
            parse_mode="HTML"
        )
    
    return ConversationHandler.END


async def handle_tts_ref_audio_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 TTS 参考音频路径输入"""
    path = update.message.text.strip()
    await config_service.set_value("tts_ref_audio_path", path)
    
    await update.message.reply_text(f"✅ 参考音频路径已设置为: {path}")
    
    # 返回主菜单
    overview = await get_dashboard_overview_text(update.effective_chat.id)
    panel_id = context.user_data.get('last_panel_id')
    
    if panel_id:
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=panel_id,
            text=overview,
            reply_markup=get_main_menu_keyboard(),
            parse_mode="HTML"
        )
    
    return ConversationHandler.END


async def handle_tts_ref_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 TTS 参考音频文本输入"""
    text = update.message.text.strip()
    await config_service.set_value("tts_ref_text", text)
    
    await update.message.reply_text(f"✅ 参考音频文本已设置为: {text}")
    
    # 返回主菜单
    overview = await get_dashboard_overview_text(update.effective_chat.id)
    panel_id = context.user_data.get('last_panel_id')
    
    if panel_id:
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=panel_id,
            text=overview,
            reply_markup=get_main_menu_keyboard(),
            parse_mode="HTML"
        )
    
    return ConversationHandler.END


async def handle_tts_lang_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 TTS 语言输入"""
    lang = update.message.text.strip().lower()
    
    # 验证语言代码
    if lang not in ["zh", "en", "ja", "ko"]:
        await update.message.reply_text("⚠️ 无效的语言代码，请输入 zh、en、ja 或 ko")
        return ConversationHandler.END
    
    await config_service.set_value("tts_text_lang", lang)
    await config_service.set_value("tts_prompt_lang", lang)  # 同时设置 prompt_lang
    
    await update.message.reply_text(f"✅ TTS 语言已设置为: {lang}")
    
    # 返回主菜单
    overview = await get_dashboard_overview_text(update.effective_chat.id)
    panel_id = context.user_data.get('last_panel_id')
    
    if panel_id:
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=panel_id,
            text=overview,
            reply_markup=get_main_menu_keyboard(),
            parse_mode="HTML"
        )
    
    return ConversationHandler.END


async def handle_tts_speed_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 TTS 语速倍率输入"""
    try:
        speed = float(update.message.text.strip())
        
        # 验证范围
        if speed < 0.5 or speed > 2.0:
            await update.message.reply_text("⚠️ 语速倍率必须在 0.5 - 2.0 之间")
            return ConversationHandler.END
        
        await config_service.set_value("tts_speed_factor", str(speed))
        await update.message.reply_text(f"✅ 语速倍率已设置为: {speed}")
        
    except ValueError:
        await update.message.reply_text("⚠️ 无效的数字格式，请输入 0.5 - 2.0 之间的数字")
        return ConversationHandler.END
    
    # 返回主菜单
    overview = await get_dashboard_overview_text(update.effective_chat.id)
    panel_id = context.user_data.get('last_panel_id')
    
    if panel_id:
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=panel_id,
            text=overview,
            reply_markup=get_main_menu_keyboard(),
            parse_mode="HTML"
        )
    
    return ConversationHandler.END

async def handle_tts_prompt_lang_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 TTS 参考音频语言输入"""
    lang = update.message.text.strip().lower()
    
    # 验证语言代码
    if lang not in ["zh", "en", "ja", "ko"]:
        await update.message.reply_text("⚠️ 无效的语言代码，请输入 zh、en、ja ...")
        return ConversationHandler.END
    
    await config_service.set_value("tts_prompt_lang", lang)
    
    await update.message.reply_text(f"✅ 参考音频语言 (Prompt Lang) 已设置为: {lang}")
    
    # 返回主菜单
    overview = await get_dashboard_overview_text(update.effective_chat.id)
    panel_id = context.user_data.get('last_panel_id')
    
    if panel_id:
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=panel_id,
            text=overview,
            reply_markup=get_main_menu_keyboard(),
            parse_mode="HTML"
        )
    
    return ConversationHandler.END
