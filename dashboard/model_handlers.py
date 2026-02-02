from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler
from core.llm_utils import fetch_available_models
from core.config_service import config_service
from dashboard.keyboards import (
    get_main_menu_keyboard, 
    get_alphabet_keyboard, 
    get_provider_list_keyboard, 
    get_model_selection_keyboard_v2
)
from dashboard.states import WAITING_INPUT_MODEL_SEARCH, WAITING_INPUT_MODEL_NAME
from dashboard.handlers import get_dashboard_overview_text
# 避免循环导入
from dashboard.input_handlers import _try_delete_previous_panel

# 模型缓存
_model_cache = {}

# 导航状态缓存
_nav_state = {}

async def show_model_selection_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, target: str = "main", header_text: str = None):
    """
    统一处理模型回调
    """
    user_id = update.effective_user.id
    
    # 保存选择目标
    context.user_data['model_selection_target'] = target
    
    # 1. 确保模型数据已加载
    if user_id not in _model_cache:
        status_msg = None
        loading_text = "🔄 正在从供应商获取模型列表，请稍候..."
        
        if update.callback_query:
            await update.callback_query.edit_message_text(loading_text)
        else:
            status_msg = await update.message.reply_text(loading_text)

        success, result = await fetch_available_models()
        
        if success:
            _model_cache[user_id] = result
        else:
            # 失败处理
            _model_cache.pop(user_id, None)
            text = f"⚠️ 无法获取模型列表: {result}\n\n请直接手动输入模型名称:"
            if update.callback_query:
                await update.callback_query.edit_message_text(text, parse_mode="HTML")
            elif status_msg:
                await status_msg.edit_text(text, parse_mode="HTML")
            else:
                await update.message.reply_text(text, parse_mode="HTML")
            return
            
    # 2. 展示键盘
    if target == 'summary':
        target_display = "Summary"
    elif target == 'asr':
        target_display = "ASR"
    else:
        target_display = "Main"
    
    # 使用自定义标题
    if header_text:
        text = header_text
    else:
        text = (
            f"<b>🤖 模型选择 ({target_display}) (1/3): 索引</b>\n\n"
            "为了快速查找，请选择 **供应商名称** 的首字母：\n"
            f"(已加载 {_get_model_count(user_id)} 个模型)"
        )
        
    keyboard = get_alphabet_keyboard(target=target)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")

async def handle_model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    统一处理所有 model_ 前缀的回调
    """
    query = update.callback_query
    data = query.data
    user_id = update.effective_user.id
    
    # 鉴权
    from core.secure import is_admin
    if not is_admin(user_id):
        await query.answer("Access Denied", show_alert=True)
        return ConversationHandler.END
    
    # 读取目标
    target = context.user_data.get('model_selection_target', 'main')
    
    if target == 'summary':
        target_display = "Summary"
    elif target == 'asr':
        target_display = "ASR"
    else:
        target_display = "Main"
    
    # 检查缓存
    if user_id not in _model_cache and data != "model_idx_back":
        # 尝试重新加载，或者提示用户重试
        await show_model_selection_panel(update, context, target=target)
        return

    # Level 1: 字母选择
    if data.startswith("model_idx:"):
        char = data.split(":")[1]
        _update_nav_state(user_id, char=char, search_query=None) # Clear search
        await _show_provider_list(update, user_id, char, target_display)
        return

    # 返回索引
    if data == "model_idx_back":
        _update_nav_state(user_id, search_query=None) # Clear search
        await show_model_selection_panel(update, context, target=target)
        return

    # Level 2: 厂商选择
    if data.startswith("model_prov:"):
        prov = data.split(":")[1]
        _update_nav_state(user_id, provider=prov, page=0) # 选中厂商，重置页码
        await _show_model_list(update, user_id, prov, 0, target_display)
        return

    # 返回厂商列表
    if data == "model_prov_back":
        # 回退到厂商列表，需要知道刚才选的是哪个字母
        state = _nav_state.get(user_id, {})
        char = state.get("char", "A") # Fallback to A
        await _show_provider_list(update, user_id, char, target_display)
        return

    # Level 3: 模型翻页
    if data.startswith("model_page_v2:"):
        page = int(data.split(":")[1])
        
        state = _nav_state.get(user_id, {})
        search_query = state.get("search_query")
        
        if search_query:
            # 搜索模式下的翻页
            await _show_search_results(update, user_id, search_query, page=page)
        else:
            # 普通厂商模式下的翻页
            prov = state.get("provider", "openai")
            await _show_model_list(update, user_id, prov, page, target_display)
        return

    # 特殊动作: 跳过摘要模型
    if data == "skip_summary_model":
        await config_service.set_value("summary_model_name", "")
        
        # 清理缓存
        _model_cache.pop(user_id, None)
        _nav_state.pop(user_id, None)
        context.user_data.pop('model_selection_target', None)
        
        # 1. Separate Notification
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="✅ [Summary] 已重置 (跟随主模型)",
            parse_mode="HTML"
        )
        
        # 2. Reset Panel to Overview
        overview_text = await get_dashboard_overview_text(update.effective_chat.id)
        await query.edit_message_text(
            overview_text,
            reply_markup=get_main_menu_keyboard(),
            parse_mode="HTML"
        )
        return ConversationHandler.END

    # Level 3: 最终选择
    if data.startswith("model_sel:"):
        model_name = data.split(":", 1)[1] # 这里的 split 1 很重要，防止模型名里有冒号
        
        if target == 'summary':
            await config_service.set_value("summary_model_name", model_name)
            msg_text = f"✅ [Summary] 模型已切换为: <code>{model_name}</code>"
        elif target == 'asr':
            await config_service.set_value("asr_model_name", model_name)
            msg_text = f"✅ [ASR] 模型已切换为: <code>{model_name}</code>"
        else:
            await config_service.set_value("model_name", model_name)
            msg_text = f"✅ [Main] 模型已切换为: <code>{model_name}</code>"
        
        # 清理缓存
        _model_cache.pop(user_id, None)
        _nav_state.pop(user_id, None)
        context.user_data.pop('model_selection_target', None)
        
        # 1. Separate Notification
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=msg_text,
            parse_mode="HTML"
        )

        # 2. Reset Panel to Overview
        overview_text = await get_dashboard_overview_text(update.effective_chat.id)
        await query.edit_message_text(
            overview_text,
            reply_markup=get_main_menu_keyboard(),
            parse_mode="HTML"
        )
        # End Conversation
        return ConversationHandler.END

    if data == "noop_manual_hint":
        await query.answer("请在输入框直接发送模型名称", show_alert=True)
        return None

    if data == "close_dashboard" or data == "cancel_input":
        await query.delete_message()
        return ConversationHandler.END


    if data == "trigger_model_search":
        await query.edit_message_text(
            "🔍 <b>模型搜索</b>\n\n请输入关键词 (支持模糊匹配):",
            parse_mode="HTML"
        )
        return WAITING_INPUT_MODEL_SEARCH
        
    await query.answer("Unknown action")
    return None

async def perform_model_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    执行搜索
    """
    await _try_delete_previous_panel(context, update.effective_chat.id)
    
    user_id = update.effective_user.id
    query_text = update.message.text.strip().lower()
    
    # 确保缓存存在
    if user_id not in _model_cache:
        # 尝试重新 fetch
        success, result = await fetch_available_models()
        if success:
            _model_cache[user_id] = result
        else:
            await update.message.reply_text("⚠️ 无法获取模型列表，请稍后重试。")
            return ConversationHandler.END
            
    # 保存状态
    _update_nav_state(user_id, search_query=query_text, page=0)
    
    await _show_search_results(update, user_id, query_text, page=0)
    
    return WAITING_INPUT_MODEL_SEARCH

# --- Helpers ---

async def _show_provider_list(update: Update, user_id: int, char: str, target_display: str = "Main"):
    """展示属于该首字母的 Provider 列表"""
    models = _model_cache.get(user_id, [])
    # 提取厂商
    
    providers = set()
    for m in models:
        if '/' in m:
            p = m.split('/')[0]
        else:
            p = "other"
            
        if p.upper().startswith(char):
            providers.add(p)
            
    sorted_provs = sorted(list(providers))
    
    if not sorted_provs:
        await update.callback_query.answer(f"未找到以 {char} 开头的供应商", show_alert=True)
        return

    keyboard = get_provider_list_keyboard(sorted_provs)
    text = (
        f"<b>🤖 模型选择 ({target_display}) (2/3): 供应商</b>\n\n"
        f"索引: <b>{char}</b>\n"
        "请选择模型供应商："
    )
    await update.callback_query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")

async def _show_model_list(update: Update, user_id: int, provider: str, page: int, target_display: str = "Main"):
    """展示特定 Provider 的模型"""
    all_models = _model_cache.get(user_id, [])
    
    # 筛选
    target_models = []
    for m in all_models:
        if provider == "other":
            if '/' not in m:
                target_models.append(m)
        else:
            if m.startswith(f"{provider}/"):
                target_models.append(m)
                
    # 排序
    target_models.sort()
    
    keyboard = get_model_selection_keyboard_v2(target_models, page=page)
    text = (
        f"<b>🤖 模型选择 ({target_display}) (3/3): 模型</b>\n\n"
        f"供应商: <b>{provider}</b>\n"
        f"共找到 {len(target_models)} 个模型。\n"
        "请点击选择："
    )
    await update.callback_query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")

async def _show_search_results(update: Update, user_id: int, query_text: str, page: int):
    """展示搜索结果"""
    all_models = _model_cache.get(user_id, [])
    
    # Filter
    results = [m for m in all_models if query_text in m.lower()]
    results.sort()
    
    keyboard = get_model_selection_keyboard_v2(results, page=page, back_callback="model_idx_back")
    # 注意：get_model_selection_keyboard_v2 默认有 "返回厂商" 按钮。
    # 但在搜索模式下，返回厂商可能不合适？或者我们暂且留着它，它会回到 "model_prov_back" -> index?
    # 我们最好不管它，或者在此处 hack 一下 keyboard
    
    text = (
        f"<b>🔍 搜索结果</b>\n\n"
        f"关键词: <code>{query_text}</code>\n"
        f"找到 {len(results)} 个匹配项。\n"
    )
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")

def _update_nav_state(user_id: int, **kwargs):
    if user_id not in _nav_state:
        _nav_state[user_id] = {}
    _nav_state[user_id].update(kwargs)

def _get_model_count(user_id):
    return len(_model_cache.get(user_id, []))
