from telegram import InlineKeyboardButton, InlineKeyboardMarkup

def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("📡 API 设置", callback_data="menu_api"),
            InlineKeyboardButton("🧠 人格与指令", callback_data="menu_persona")
        ],
        [
            InlineKeyboardButton("🛡️ 访问控制", callback_data="menu_access"),
            InlineKeyboardButton("🧹 记忆管理", callback_data="menu_memory")
        ],
        [
            InlineKeyboardButton("🎤 语音配置 (Voice)", callback_data="menu_voice")
        ],
        [
            InlineKeyboardButton("📺 主动消息 (Active Push)", callback_data="menu_agentic")
        ],
        [
            InlineKeyboardButton("❌ 关闭面板", callback_data="close_dashboard")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

# ... (Existing API settings ... )

def get_agentic_keyboard() -> InlineKeyboardMarkup:
    """自主意识菜单"""
    keyboard = [
        [InlineKeyboardButton("➕ 添加订阅源 (/sub)", callback_data="add_sub_request")],
        [InlineKeyboardButton("📋 管理订阅列表", callback_data="list_subs")],
        [InlineKeyboardButton("⏰ 设置活跃时间 (DND)", callback_data="set_active_time")],
        [InlineKeyboardButton("💤 设置闲置阈值 (Idle)", callback_data="set_idle_time")],
        [InlineKeyboardButton("🔙 返回主菜单", callback_data="menu_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_api_settings_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("🔗 设置 Base URL", callback_data="set_api_url")],
        [InlineKeyboardButton("🔑 设置 API Key", callback_data="set_api_key")],
        [InlineKeyboardButton("🤖 设置主模型 (Main)", callback_data="set_model_name")],
        [InlineKeyboardButton("🧬 设置向量模型 (Vector)", callback_data="set_vector_model")],
        [InlineKeyboardButton("🧠 设置摘要模型 (Summary)", callback_data="set_summary_model")],
        [InlineKeyboardButton("📷 设置媒体模型 (Media)", callback_data="set_media_model")],
        [InlineKeyboardButton("⏳ 设置聚合延迟 (Debounce)", callback_data="set_aggregation_latency")],
        [InlineKeyboardButton("🔙 返回主菜单", callback_data="menu_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_persona_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("📝 修改 System Prompt", callback_data="set_sys_prompt")],
        [InlineKeyboardButton("🔥 调整 Temperature", callback_data="set_temperature")],
        [InlineKeyboardButton("🔙 返回主菜单", callback_data="menu_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_access_control_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("➕ 添加白名单 ID", callback_data="add_whitelist_id")],
        [InlineKeyboardButton("➖ 移除白名单 ID", callback_data="remove_whitelist_id")],
        [InlineKeyboardButton("📋由于空间有限，列表请直接点击查看", callback_data="list_whitelist")],
        [InlineKeyboardButton("🔙 返回主菜单", callback_data="menu_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

    return InlineKeyboardMarkup(keyboard)

def get_memory_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("🔮 RAG 高级设置 (Vector)", callback_data="menu_rag")],
        [InlineKeyboardButton("🔢 设置记忆长度 (T)", callback_data="set_history_tokens")],
        [InlineKeyboardButton("🚨 恢复出厂设置 (Danger)", callback_data="factory_reset_request")],
        [InlineKeyboardButton("🔙 返回主菜单", callback_data="menu_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def get_rag_settings_keyboard() -> InlineKeyboardMarkup:
    """RAG 设置菜单 (动态读取当前值)"""
    from core.config_service import config_service
    
    # 读取当前配置
    cooldown = await config_service.get_value("rag_sync_cooldown", "180")
    threshold = await config_service.get_value("rag_similarity_threshold", "0.6")
    padding = await config_service.get_value("rag_context_padding", "3")
    
    keyboard = [
        # Values Row
        [
            InlineKeyboardButton(f"⏱️ 冷却时间: {cooldown}s", callback_data="trigger_set_rag_cd"),
        ],
        [
            InlineKeyboardButton(f"🎯 相似度阈值: {threshold}", callback_data="trigger_set_rag_th"),
        ],
        [
            InlineKeyboardButton(f"↔️ 拓展窗口: {padding}", callback_data="trigger_set_rag_padding"),
        ],
        [InlineKeyboardButton("🧨 Rebuild Index (Danger)", callback_data="trigger_rebuild_index")],
        [InlineKeyboardButton("🔙 返回设置", callback_data="menu_memory")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def get_voice_keyboard() -> InlineKeyboardMarkup:
    """语音配置菜单 (动态)"""
    from core.config_service import config_service
    
    # 动态获取 TTS 状态
    tts_enabled = await config_service.get_value("tts_enabled", "false")
    is_enabled = str(tts_enabled).strip().lower() in ("true", "1", "yes")
    
    toggle_text = "✅ 禁用 TTS (Enabled)" if is_enabled else "❌ 启用 TTS (Disabled)"
    toggle_data = "toggle_tts"
    
    keyboard = [

        [InlineKeyboardButton("🔊 配置 TTS (URL)", callback_data="set_tts_url")],
        [InlineKeyboardButton("🎵 配置参考音频", callback_data="set_tts_ref_audio")],
        [InlineKeyboardButton("📝 配置参考文本", callback_data="set_tts_ref_text")],
        [InlineKeyboardButton("🌐 设置 TTS 语言 (Target)", callback_data="set_tts_lang")],
        [InlineKeyboardButton("🗣️ 设置参考语言 (Prompt)", callback_data="set_tts_prompt_lang")],
        [InlineKeyboardButton("⚡ 设置语速倍率", callback_data="set_tts_speed")],
        [InlineKeyboardButton(toggle_text, callback_data=toggle_data)],  # 动态文本
        [InlineKeyboardButton("🔙 返回主菜单", callback_data="menu_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_alphabet_keyboard(target: str = "main") -> InlineKeyboardMarkup:
    """
    一级：字母索引
    """
    import string
    chars = string.ascii_uppercase
    
    keyboard = []
    
    
    # 摘要模式显示跳过
    if target == "summary":
        keyboard.append([InlineKeyboardButton("⏭️ 使用主模型 (默认)", callback_data="skip_summary_model")])
        
    row = []
    for char in chars:
        row.append(InlineKeyboardButton(char, callback_data=f"model_idx:{char}"))
        if len(row) == 5:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
        
    # 添加 Only Text 选项和关闭
    keyboard.append([
        InlineKeyboardButton("🔍 搜索模型", callback_data="trigger_model_search"),
        InlineKeyboardButton("✍️ 手动输入", callback_data="noop_manual_hint")
    ])
    keyboard.append([InlineKeyboardButton("🔙 关闭", callback_data="close_dashboard")])
    
    return InlineKeyboardMarkup(keyboard)

def get_provider_list_keyboard(providers: list[str]) -> InlineKeyboardMarkup:
    """
    二级：厂商列表
    """
    keyboard = []
    for prov in providers:
        # 显示完整 Vendor 名
        keyboard.append([InlineKeyboardButton(f"🏢 {prov}", callback_data=f"model_prov:{prov}")])
    
    keyboard.append([InlineKeyboardButton("⬅️ 返回索引", callback_data="model_idx_back")])
    return InlineKeyboardMarkup(keyboard)

def get_model_selection_keyboard_v2(models: list[str], page: int = 0, items_per_page: int = 10, back_callback: str = "model_prov_back") -> InlineKeyboardMarkup:
    """
    三级：模型列表 (分页)
    """
    total_models = len(models)
    start_idx = page * items_per_page
    end_idx = min(start_idx + items_per_page, total_models)
    
    current_page_models = models[start_idx:end_idx]
    
    keyboard = []
    
    for model_id in current_page_models:
        # 精简显示名称
        display_name = model_id.split('/')[-1] if '/' in model_id else model_id
        if len(display_name) > 30:
            display_name = display_name[:28] + ".."
            
        # 避免模型名过长导致 Callback 溢出 (暂未处理)
        
        keyboard.append([InlineKeyboardButton(f"🤖 {display_name}", callback_data=f"model_sel:{model_id}")])
    
    nav_buttons = []
    total_pages = (total_models + items_per_page - 1) // items_per_page
    
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️", callback_data=f"model_page_v2:{page-1}"))
    
    nav_buttons.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="noop"))
    
    if end_idx < total_models:
        nav_buttons.append(InlineKeyboardButton("➡️", callback_data=f"model_page_v2:{page+1}"))
        
    if nav_buttons:
        keyboard.append(nav_buttons)
        
    keyboard.append([InlineKeyboardButton("⬅️ 返回", callback_data=back_callback)])
    
    return InlineKeyboardMarkup(keyboard)

def get_cancel_keyboard() -> InlineKeyboardMarkup:
    """通用取消按钮"""
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 取消 (Cancel)", callback_data="cancel_input")]])
