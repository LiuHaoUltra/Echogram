from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from core.history_service import history_service
from core.access_service import access_service
from core.config_service import config_service
from core.summary_service import summary_service
from utils.prompts import prompt_builder
from dashboard.keyboards import (
    get_main_menu_keyboard,
    get_api_settings_keyboard,
    get_persona_keyboard,
    get_access_control_keyboard,
    get_memory_keyboard
)
from dashboard.states import (
    WAITING_INPUT_API_URL, WAITING_INPUT_API_KEY, WAITING_INPUT_MODEL_NAME,
    WAITING_INPUT_SYSTEM_PROMPT, WAITING_INPUT_WHITELIST_ADD, WAITING_INPUT_WHITELIST_REMOVE,
    WAITING_INPUT_SUMMARY_MODEL, WAITING_INPUT_HISTORY_TOKENS
)
from dashboard.model_handlers import show_model_selection_panel

async def menu_navigation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    
    # 鉴权: 即使有人转发了面板，非管理员点击也应无效/静默
    from core.secure import is_admin
    if not is_admin(update.effective_user.id):
        await query.answer("Access Denied", show_alert=True) # 或者完全静默，但 callback 最好 answer 一下防止转圈
        return ConversationHandler.END

    await query.answer()
    data = query.data

    # --- 通用导航 ---
    if data == "close_dashboard":
        await query.delete_message()
        return ConversationHandler.END

    if data == "menu_main" or data == "cancel_input":
        # Avoid circular import by importing inside function or ensure structure allows it
        from dashboard.handlers import get_dashboard_overview_text
        overview_text = await get_dashboard_overview_text(update.effective_chat.id)
        
        await query.edit_message_text(
            text=overview_text,
            reply_markup=get_main_menu_keyboard(),
            parse_mode="HTML"
        )
        return ConversationHandler.END

    # --- 1. API 菜单 ---
    if data == "menu_api":
        await query.edit_message_text(text="<b>📡 API 设置</b>", reply_markup=get_api_settings_keyboard(), parse_mode="HTML")
        return ConversationHandler.END
    
    if data == "set_api_url":
        await query.edit_message_text(text="请输入新的 <b>Base URL</b>:", parse_mode="HTML")
        return WAITING_INPUT_API_URL
    if data == "set_api_key":
        await query.edit_message_text(text="请输入新的 <b>API Key</b>:", parse_mode="HTML")
        return WAITING_INPUT_API_KEY
    if data == "set_model_name":
        # 即使是 Dashboard 修改，也展示面板
        # target='main' is default, but explicit is better
        await show_model_selection_panel(update, context, target="main")
        return WAITING_INPUT_MODEL_NAME
        
    if data == "set_summary_model":
        await show_model_selection_panel(update, context, target="summary")
        return WAITING_INPUT_SUMMARY_MODEL
        
    if data == "set_summary_model":
        await show_model_selection_panel(update, context, target="summary")
        return WAITING_INPUT_SUMMARY_MODEL
    
    if data == "set_aggregation_latency":
        current_val = await config_service.get_value("aggregation_latency", "10")
        await query.edit_message_text(
            text=f"请输入新的 <b>聚合延迟 (秒)</b>:\n当前值: {current_val} s\n(建议 3-10 秒)", 
            parse_mode="HTML"
        )
        return WAITING_INPUT_AGGREGATION_LATENCY

    # --- 2. 人格菜单 ---
    if data == "menu_persona":
        current_prompt = await config_service.get_value("system_prompt", "未设置")
        display_prompt = (current_prompt[:50] + '...') if len(current_prompt) > 50 else current_prompt
        await query.edit_message_text(
            text=f"<b>🧠 人格设置</b>\n当前 System Prompt:\n<pre>{display_prompt}</pre>",
            reply_markup=get_persona_keyboard(),
            parse_mode="HTML"
        )
        return ConversationHandler.END

    if data == "set_sys_prompt":
        await query.edit_message_text(text="请输入新的 <b>System Prompt</b>:", parse_mode="HTML")
        return WAITING_INPUT_SYSTEM_PROMPT

    # --- 3. 访问控制 ---
    if data == "menu_access":
        await query.edit_message_text(text="<b>🛡️ 访问控制</b>", reply_markup=get_access_control_keyboard(), parse_mode="HTML")
        return ConversationHandler.END

    if data == "list_whitelist":
        items = await access_service.get_all_whitelist()
        text = "<b>📜 白名单列表:</b>\n\n"
        if not items:
            text += "暂无数据"
        else:
            for item in items:
                text += f"• <code>{item.chat_id}</code> ({item.type})\n"
        # 列表太长可能需要分页，暂且直接显示
        # 注意：这里我们覆盖了原文，提供了返回按钮
        await query.edit_message_text(text=text, reply_markup=get_access_control_keyboard(), parse_mode="HTML")
        return ConversationHandler.END

    if data == "add_whitelist_id":
        await query.edit_message_text(text="请输入要添加的 <b>Chat ID</b>:", parse_mode="HTML")
        return WAITING_INPUT_WHITELIST_ADD
    
    if data == "remove_whitelist_id":
        await query.edit_message_text(text="请输入要移除的 <b>Chat ID</b>:", parse_mode="HTML")
        return WAITING_INPUT_WHITELIST_REMOVE

    # --- 4. 记忆管理 ---
    if data == "menu_memory":
        await query.edit_message_text(text="<b>🧹 记忆管理</b>", reply_markup=get_memory_keyboard(), parse_mode="HTML")
        return ConversationHandler.END
    
    # Removed old text-based set_summary_model handler block from here since it is now handled above via panel

    
    
    if data == "set_history_tokens":
        from config.settings import settings
        current_val = await config_service.get_value("history_tokens", str(settings.HISTORY_WINDOW_TOKENS))
        await query.edit_message_text(
            text=f"请输入新的 <b>历史记录 Token 上限</b>:\n当前值: {current_val}\n(默认: {settings.HISTORY_WINDOW_TOKENS}，建议 2000-16000)",
            parse_mode="HTML"
        )
        return WAITING_INPUT_HISTORY_TOKENS
    
    if data == "factory_reset_request":
        # 危险操作 Warning
        keyboard = [
            [InlineKeyboardButton("🛑 确认清空所有数据 (不可恢复)", callback_data="factory_reset_confirm")],
            [InlineKeyboardButton("🔙 取消", callback_data="menu_memory")]
        ]
        await query.edit_message_text(
            text="<b>⚠️ 严重警告 (Danger Zone)</b>\n\n您正在请求执行 <b>恢复出厂设置</b>。\n此操作将：\n1. 清空所有对话历史\n2. 清空所有长期记忆摘要\n3. 清空所有配置 (包括API Key)\n4. 清空白名单\n\nBot 将需要重新初始化。确定继续吗？",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        return ConversationHandler.END

    if data == "factory_reset_confirm":
        # 执行重置
        await history_service.factory_reset()
        await summary_service.factory_reset()
        await config_service.factory_reset()
        await access_service.factory_reset()
        
        await query.edit_message_text(
            text="<b>✅ 重置完成 (Factory Reset Complete)</b>\n\n所有数据已清除。请发送 /start 重新开始设置向导。",
            parse_mode="HTML"
        )
        return ConversationHandler.END

        return ConversationHandler.END

    if data == "preview_sys_prompt":
        import html
        # 获取当前 Chat 的配置
        chat_id = update.effective_chat.id
        
        # 1. 获取动态侧写 (Summary)
        dynamic_summary = await summary_service.get_summary(chat_id)
        
        # 2. 获取自定义 System Prompt (Soul)
        soul_prompt = await config_service.get_value("system_prompt")
        
        # 3. 获取时区
        timezone = await config_service.get_value("timezone", "UTC")
        
        # 4. 组装完整 Prompt
        full_prompt = prompt_builder.build_system_prompt(
            soul_prompt=soul_prompt,
            timezone=timezone,
            dynamic_summary=dynamic_summary
        )
        
        # 5. 显示 (使用 <pre> 保持格式)
        # 由于 Prompt 可能很长，Telegram 消息限制 4096 字符。
        # 如果超长，进行截断或分段。这里做简单处理。
        # [Security] HTML Escape to prevent parse errors with tags like <chat>
        safe_prompt = html.escape(full_prompt)
        
        if len(safe_prompt) > 4000:
            safe_prompt = safe_prompt[:3900] + "\n\n... (Truncated)"
            
        await query.edit_message_text(
            text=f"<b>👁️ 当前提示词预览 (System Prompt)</b>\n\n<pre>{safe_prompt}</pre>",
            reply_markup=get_memory_keyboard(),
            parse_mode="HTML"
        )
        return ConversationHandler.END

    return ConversationHandler.END
