from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler
from core.history_service import history_service
from core.access_service import access_service
from core.config_service import config_service
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
    WAITING_INPUT_AGGREGATION_LATENCY, WAITING_INPUT_CONTEXT_LIMIT,
    WAITING_INPUT_SUMMARY_MODEL
)
from dashboard.model_handlers import show_model_selection_panel

async def menu_navigation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
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

    
    if data == "set_context_limit":
        current_val = await config_service.get_value("context_limit", "30")
        await query.edit_message_text(
            text=f"请输入新的 <b>上下文消息数量上限</b>:\n当前值: {current_val}\n(建议 5-50，过大会消耗大量 Token)",
            parse_mode="HTML"
        )
        return WAITING_INPUT_CONTEXT_LIMIT
    
    if data == "clear_context_confirm":
        # 清空记忆：假定清空当前用户（如果是私聊）或需要指定？
        # 基于PRD：Context是基于 chat_id 的。
        # 如果是在私聊 Dashboard 中点清除，通常由于 Dashboard 和 Chat 是两个概念，
        # 我们这里暂时默认清除“当前与Bot私聊”的记忆，或者 Bot 无法知道你想清除哪个群的。
        # 改进：提示 "只能清除当前会话(私聊)的记忆"。
        # 但 PRD 的场景是 Admin 用私聊控制 Bot。
        # 如果 Admin 想清除某个群的记忆，可能需要输入群ID。
        # 为了简单，我们先实现清除当前会话（Private Chat with Admin）的记忆。
        await history_service.clear_context(update.effective_chat.id)
        await query.answer("✅ 当前会话记忆已清空", show_alert=True)
        return ConversationHandler.END

    return ConversationHandler.END
