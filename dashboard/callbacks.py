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
    get_memory_keyboard,
    get_cancel_keyboard
)
from dashboard.states import (
    WAITING_INPUT_API_URL, WAITING_INPUT_API_KEY, WAITING_INPUT_MODEL_NAME,
    WAITING_INPUT_SYSTEM_PROMPT, WAITING_INPUT_WHITELIST_ADD, WAITING_INPUT_WHITELIST_REMOVE,
    WAITING_INPUT_AGGREGATION_LATENCY,
    WAITING_INPUT_SUMMARY_MODEL, WAITING_INPUT_HISTORY_TOKENS, WAITING_INPUT_TEMPERATURE
)
from dashboard.model_handlers import show_model_selection_panel

async def menu_navigation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    
    # 鉴权: 防转发
    from core.secure import is_admin
    if not is_admin(update.effective_user.id):
        await query.answer("Access Denied", show_alert=True)
        return ConversationHandler.END

    await query.answer()
    data = query.data

    # --- 通用导航 ---
    if data == "close_dashboard":
        await query.delete_message()
        return ConversationHandler.END

    if data == "menu_main" or data == "cancel_input":
        # 防止循环导入
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
        await query.edit_message_text(text="请输入新的 <b>Base URL</b>:", reply_markup=get_cancel_keyboard(), parse_mode="HTML")
        context.user_data['last_panel_id'] = query.message.message_id
        return WAITING_INPUT_API_URL
    if data == "set_api_key":
        await query.edit_message_text(text="请输入新的 <b>API Key</b>:", reply_markup=get_cancel_keyboard(), parse_mode="HTML")
        context.user_data['last_panel_id'] = query.message.message_id
        return WAITING_INPUT_API_KEY
    if data == "set_model_name":
        # 即使是 Dashboard 修改，也展示面板
        await show_model_selection_panel(update, context, target="main")
        return WAITING_INPUT_MODEL_NAME
        
    if data == "set_summary_model":
        await show_model_selection_panel(update, context, target="summary")
        return WAITING_INPUT_SUMMARY_MODEL
    
    if data == "set_aggregation_latency":
        current_val = await config_service.get_value("aggregation_latency", "10")
        await query.edit_message_text(
            text=f"请输入新的 <b>聚合延迟 (秒)</b>:\n当前值: {current_val} s\n(建议 5-20 秒)", 
            reply_markup=get_cancel_keyboard(),
            parse_mode="HTML"
        )
        context.user_data['last_panel_id'] = query.message.message_id
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
        await query.edit_message_text(text="请输入新的 <b>System Prompt</b>:", reply_markup=get_cancel_keyboard(), parse_mode="HTML")
        context.user_data['last_panel_id'] = query.message.message_id
        return WAITING_INPUT_SYSTEM_PROMPT

    if data == "set_temperature":
        current_val = await config_service.get_value("temperature", "0.7")
        await query.edit_message_text(
            text=(
                f"🔥 <b>调整采样温度 (Temperature)</b>\n\n"
                f"当前值: <code>{current_val}</code>\n\n"
                "此参数决定回复的<b>随机性</b>：\n"
                "• <b>0.0 - 0.3</b>：稳定且理性，适合逻辑处理。\n"
                "• <b>0.7 - 0.8</b>：默认值，兼顾连贯与创造力。\n"
                "• <b>0.9 - 1.0</b>：极其发散，可能胡言乱语。\n\n"
                "请输入 0.0 ~ 1.0 之间的数字："
            ),
            reply_markup=get_cancel_keyboard(),
            parse_mode="HTML"
        )
        context.user_data['last_panel_id'] = query.message.message_id
        return WAITING_INPUT_TEMPERATURE

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
                name_disp = f" ({item.description})" if item.description else ""
                text += f"• <code>{item.chat_id}</code>{name_disp} [{item.type}]\n"
        # 暂无分页，直接显示
        await query.edit_message_text(text=text, reply_markup=get_access_control_keyboard(), parse_mode="HTML")
        return ConversationHandler.END

    if data == "add_whitelist_id":
        await query.edit_message_text(text="请输入要添加的 <b>Chat ID</b>:", reply_markup=get_cancel_keyboard(), parse_mode="HTML")
        context.user_data['last_panel_id'] = query.message.message_id
        return WAITING_INPUT_WHITELIST_ADD
    
    if data == "remove_whitelist_id":
        await query.edit_message_text(text="请输入要移除的 <b>Chat ID</b>:", reply_markup=get_cancel_keyboard(), parse_mode="HTML")
        context.user_data['last_panel_id'] = query.message.message_id
        return WAITING_INPUT_WHITELIST_REMOVE

    # --- 4. 记忆管理 ---
    if data == "menu_memory":
        try:
            await query.edit_message_text(text="<b>🧹 记忆管理</b>", reply_markup=get_memory_keyboard(), parse_mode="HTML")
        except Exception as e:
            if "Message is not modified" not in str(e):
                raise e
        return ConversationHandler.END
    
    
    if data == "set_history_tokens":
        from config.settings import settings
        current_val = await config_service.get_value("history_tokens", str(settings.HISTORY_WINDOW_TOKENS))
        await query.edit_message_text(
            text=(
                f"🔢 <b>设置对话记忆长度 (Threshold T)</b>\n\n"
                f"当前值: <code>{current_val}</code>\n\n"
                "此参数决定两个核心逻辑：\n"
                "1. <b>活跃记忆</b>：AI 始终能看到最近 T 个 Token 的原始对话。\n"
                "2. <b>归档触发</b>：当“溢出”出活跃窗口的消息也达到 T 个 Token 时，将自动触发一次远程归档（总结）。\n\n"
                "📊 <i>建议值：500 - 8000 (根据模型能力决定)</i>\n"
                "请直接发送数字："
            ),
            reply_markup=get_cancel_keyboard(),
            parse_mode="HTML"
        )
        context.user_data['last_panel_id'] = query.message.message_id
        return WAITING_INPUT_HISTORY_TOKENS
    
    if data == "factory_reset_request":
        # 危险操作警告
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

    # --- 5. 新闻推送 (News Push) 管理 ---
    if data == "menu_agentic":
        from dashboard.keyboards import get_agentic_keyboard
        await query.edit_message_text(text="<b>📺 主动消息 (Active Push)</b>", reply_markup=get_agentic_keyboard(), parse_mode="HTML")
        return ConversationHandler.END

    if data == "list_subs":
        from core.news_push_service import news_push_service
        subs = await news_push_service.get_all_subscriptions()
        if not subs:
            await query.answer("暂无订阅源", show_alert=True)
            return ConversationHandler.END
            
        # 动态生成列表按钮
        keyboard = []
        for sub in subs:
            # Status Icon
            status_icon = "✅" if sub.status == "normal" else "❌"
            error_hint = f" ({sub.last_error})" if sub.status == "error" and sub.last_error else ""
            
            # Row 1: Name & Status
            keyboard.append([InlineKeyboardButton(f"{status_icon} {sub.name}{error_hint}", callback_data="noop")])
            # Row 2: Actions
            keyboard.append([
                InlineKeyboardButton("🎯 分发对象", callback_data=f"manage_targets:{sub.id}"),
                InlineKeyboardButton("🗑️ 删除", callback_data=f"del_sub:{sub.id}")
            ])
            
        keyboard.append([InlineKeyboardButton("🔙 返回", callback_data="menu_agentic")])
        
        await query.edit_message_text(text="<b>📋 订阅源状态监控 & 管理:</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        return ConversationHandler.END

    if data.startswith("del_sub:"):
        from core.news_push_service import news_push_service
        sub_id = int(data.split(":")[1])
        await news_push_service.remove_subscription(sub_id)
        await query.answer("删除成功")
        # Return to list
        # ... (Recursively call logic or just trigger list_subs? simpler to just re-emit list logic or copy-paste)
        # For simplicity, let's just trigger a re-render by modifying data and recursively calling? No, context recursion is messy.
        # Just copy the list render logic.
        subs = await news_push_service.get_all_subscriptions()
        keyboard = []
        if subs:
            for sub in subs:
                status_icon = "✅" if sub.status == "normal" else "❌"
                keyboard.append([InlineKeyboardButton(f"{status_icon} {sub.name}", callback_data="noop")])
                keyboard.append([
                    InlineKeyboardButton("🎯 分发对象", callback_data=f"manage_targets:{sub.id}"),
                    InlineKeyboardButton("🗑️ 删除", callback_data=f"del_sub:{sub.id}")
                ])
        keyboard.append([InlineKeyboardButton("🔙 返回", callback_data="menu_agentic")])
        await query.edit_message_text(text="<b>📋 订阅源状态监控 & 管理:</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        return ConversationHandler.END
        
    if data.startswith("manage_targets:"):
        from core.news_push_service import news_push_service
        from core.access_service import access_service
        from sqlalchemy import select
        from models.news import NewsSubscription, ChatSubscription
        from config.database import get_db_session
        
        sub_id = int(data.split(":")[1])
        
        # Get Subscription Name
        sub_name = "未知"
        async for session in get_db_session():
            r = await session.execute(select(NewsSubscription).where(NewsSubscription.id == sub_id))
            obj = r.scalar_one_or_none()
            if obj: sub_name = obj.name
        
        # Get All Whitelisted Chats
        white_chats = await access_service.get_all_whitelist()
        
        # Get Linked Chats
        linked_chats = await news_push_service._get_linked_chats(sub_id)
        
        keyboard = []
        for chat in white_chats:
            is_linked = chat.chat_id in linked_chats
            check_mark = "✅" if is_linked else "⬜"
            btn_text = f"{check_mark} {chat.description or chat.chat_id}"
            # Toggle Callback
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"toggle_target:{sub_id}:{chat.chat_id}")])
            
        keyboard.append([InlineKeyboardButton("🔙 返回列表", callback_data="list_subs")])
        
        await query.edit_message_text(
            text=f"<b>🎯 分发管理: {sub_name}</b>\n点击群组以开启/关闭推送。",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        return ConversationHandler.END

    if data.startswith("toggle_target:"):
        from core.news_push_service import news_push_service
        from core.access_service import access_service
        from models.news import ChatSubscription, NewsSubscription
        from sqlalchemy import delete, select
        from config.database import get_db_session
        
        parts = data.split(":")
        sub_id = int(parts[1])
        chat_id = int(parts[2])
        
        async for session in get_db_session():
            # Check exist
            stmt = select(ChatSubscription).where(
                ChatSubscription.subscription_id == sub_id,
                ChatSubscription.chat_id == chat_id
            )
            existing = (await session.execute(stmt)).scalar_one_or_none()
            
            if existing:
                # Remove
                await session.execute(delete(ChatSubscription).where(ChatSubscription.id == existing.id))
                await session.commit()
                await query.answer(f"已移除订阅: {chat_id}")
            else:
                # Add
                new_bind = ChatSubscription(subscription_id=sub_id, chat_id=chat_id)
                session.add(new_bind)
                await session.commit()
                await query.answer(f"已添加订阅: {chat_id}")
                
        # Refresh UI (Similar to manage_targets logic)
        # Reuse logic by constructing a fake data call? 
        # Or just re-run the layout construction. 
        # Re-running is safer.
        sub_name = "未知"
        async for session in get_db_session():
            r = await session.execute(select(NewsSubscription).where(NewsSubscription.id == sub_id))
            obj = r.scalar_one_or_none()
            if obj: sub_name = obj.name

        white_chats = await access_service.get_all_whitelist()
        linked = await news_push_service._get_linked_chats(sub_id)
        
        keyboard = []
        for chat in white_chats:
            is_linked = chat.chat_id in linked
            check_mark = "✅" if is_linked else "⬜"
            btn_text = f"{check_mark} {chat.description or chat.chat_id}"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"toggle_target:{sub_id}:{chat.chat_id}")])
        keyboard.append([InlineKeyboardButton("🔙 返回列表", callback_data="list_subs")])
        
        await query.edit_message_text(
            text=f"<b>🎯 分发管理: {sub_name}</b>\n点击群组以开启/关闭推送。",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        return ConversationHandler.END

    if data == "add_sub_request":
        await query.edit_message_text(
            text=(
                "请输入 RSSHub 路由和名称。\n"
                "格式: <code>路由|名称</code>\n"
                "示例: <code>/telegram/channel/tginfo|TG Info</code>"
            ),
            reply_markup=get_cancel_keyboard(),
            parse_mode="HTML"
        )
        context.user_data['last_panel_id'] = query.message.message_id
        from dashboard.states import WAITING_INPUT_SUB_ADD
        return WAITING_INPUT_SUB_ADD

    if data == "set_active_time":
        current_start = await config_service.get_value("agentic_active_start", "08:00")
        current_end = await config_service.get_value("agentic_active_end", "23:00")
        
        await query.edit_message_text(
            text=(
                f"⏰ <b>设置活跃时间 (Active Hours)</b>\n\n"
                f"当前: <code>{current_start} - {current_end}</code>\n\n"
                "Bot 仅在此时间段内主动推送新闻。\n"
                "请输入新范围 (格式: HH:MM-HH:MM)\n"
                "示例: <code>09:00-22:00</code>"
            ),
            reply_markup=get_cancel_keyboard(),
            parse_mode="HTML"
        )
        context.user_data['last_panel_id'] = query.message.message_id
        from dashboard.states import WAITING_INPUT_ACTIVE_HOURS
        return WAITING_INPUT_ACTIVE_HOURS

    if data == "set_idle_time":
        current_val = await config_service.get_value("agentic_idle_threshold", "30")
        await query.edit_message_text(
            text=(
                f"💤 <b>设置闲置阈值 (Idle Threshold)</b>\n\n"
                f"当前: <code>{current_val} 分钟</code>\n\n"
                "Bot 仅在群组闲置超过此时间后才会推送（防插嘴）。\n"
                "请输入分钟数 (例如 60):"
            ),
            reply_markup=get_cancel_keyboard(),
            parse_mode="HTML"
        )
        context.user_data['last_panel_id'] = query.message.message_id
        from dashboard.states import WAITING_INPUT_IDLE_THRESHOLD
        return WAITING_INPUT_IDLE_THRESHOLD

    return ConversationHandler.END
