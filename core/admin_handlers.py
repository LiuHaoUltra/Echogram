from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from telegram.constants import ChatType
from core.history_service import history_service
from core.secure import is_admin, require_admin_access
from utils.logger import logger
import re
@require_admin_access
async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /reset 指令：清空当前对话的历史记忆
    """
    user = update.effective_user
    chat = update.effective_chat
    
    # 鉴权移至装饰器
    # if not is_admin(user.id): return

    # 管理员在私聊中使用：提供友好提示
    if chat.type == ChatType.PRIVATE:
        await update.message.reply_text("💡 请在群组中使用此指令，以重置该群组的会话。")
        return

    from core.chat_engine import CHAT_LOCKS
    
    # 🚨 关键：获取会话锁，防止 RAG 同步/LLM 生成期间被重置导致死锁或数据不一致
    async with CHAT_LOCKS[chat.id]:
        await history_service.clear_history(chat.id)
        # 同步清空长期摘要
        from core.summary_service import summary_service
        await summary_service.clear_summary(chat.id)
        
        # 同步清空 RAG 向量数据 (物理删除)
        from core.rag_service import rag_service
        await rag_service.clear_chat_vectors(chat.id)
    
    await update.message.reply_text("🧹 记忆已重置！上下文和长期摘要均已清空。")

@require_admin_access
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /stats 指令：查看当前会话的记忆状态
    """
    user = update.effective_user
    chat = update.effective_chat
    
    # 鉴权移至装饰器
    
    if chat.type == ChatType.PRIVATE:
        await update.message.reply_text("📊 请在群组中使用此指令查看统计信息。")
        return

    # 获取配置
    from core.config_service import config_service
    from config.settings import settings
    
    # ... (Rest of logic unchanged) ...
    # 获取动态配置
    configs = await config_service.get_all_settings()
    T = int(configs.get("history_tokens", settings.HISTORY_WINDOW_TOKENS))
    
    # 获取归档状态
    from core.summary_service import summary_service
    from core.history_service import history_service
    
    status = await summary_service.get_status(chat.id)
    last_summarized_id = status["last_id"]
    last_summary_time = status["updated_at"]
    
    # 使用统一接口获取统计数据
    stats = await history_service.get_session_stats(chat.id, T, last_summarized_id)
    active_tokens = stats["active_tokens"]
    buffer_tokens = stats["buffer_tokens"]
    
    # 进度条辅助函数
    def make_bar(current, total, length=10):
        if total <= 0: return "░" * length
        filled = int(length * (current / total))
        filled = min(filled, length)
        return "█" * filled + "░" * (length - filled)

    # 计算百分比
    active_percent = round((active_tokens / T) * 100, 1) if T > 0 else 0
    buffer_percent = round((buffer_tokens / T) * 100, 1) if T > 0 else 0
    
    # 状态判定
    session_state = "🔄 Rolling (Archiving)" if buffer_tokens > 0 else "🌱 Growing (Linear)"

    # 获取时区设定
    timezone_str = configs.get("timezone", "UTC")
    import pytz
    try:
        tz = pytz.timezone(timezone_str)
    except:
        tz = pytz.UTC

    # 格式化日期 (应用时区转换)
    if last_summary_time:
        # 如果是 naive datetime，假设其为 UTC
        if last_summary_time.tzinfo is None:
            last_summary_time = last_summary_time.replace(tzinfo=pytz.UTC)
        time_str = last_summary_time.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S")
    else:
        time_str = "Never"

    # 获取 RAG 状态
    from core.rag_service import rag_service
    rag_stats = await rag_service.get_vector_stats(chat.id)
    
    rag_indexed = rag_stats.get("indexed", 0)
    rag_pending = rag_stats.get("pending", 0)
    rag_active = rag_stats.get("active_window_size", 0)
    rag_cooldown = rag_stats.get("cooldown_left", 0)
    
    rag_status_str = "Idle"
    if rag_cooldown > 0:
        rag_status_str = f"🥶 Cooling ({rag_cooldown}s)"
    elif rag_pending > 0:
        rag_status_str = f"🚜 Processing ({rag_pending} pending)"
    
    # 简单的锁状态检查 (Non-blocking)
    from core.chat_engine import CHAT_LOCKS
    if chat.id in CHAT_LOCKS and CHAT_LOCKS[chat.id].locked():
        rag_status_str += " (Locked)"

    msg = (
        f"📊 <b>Session Statistics</b>\n\n"
        f"🆔 Chat ID: <code>{chat.id}</code>\n"
        f"📈 <b>State</b>: <code>{session_state}</code>\n\n"
        f"🧠 <b>Context Usage</b>:\n"
        f"<code>{make_bar(active_tokens, T)} {active_percent}%</code>\n"
        f"({active_tokens} / {T} tokens)\n\n"
        f"📥 <b>Archiving Buffer</b>:\n"
        f"<code>{make_bar(buffer_tokens, T)} {buffer_percent}%</code>\n"
        f"({buffer_tokens} / {T} tokens)\n\n"
        f"📚 <b>Knowledge Base (RAG)</b>:\n"
        f"• <b>Facts Indexed:</b> <code>{rag_indexed}</code>\n"
        f"• <b>Pending ETL:</b> <code>{rag_pending}</code>\n"
        f"• <b>Active Window:</b> ~{rag_active} msgs (Ignored)\n"
        f"• <b>Status:</b> {rag_status_str}\n\n"
        f" Last Summary: {time_str}"
    )
    
    await update.message.reply_text(msg, parse_mode='HTML')
@require_admin_access
async def prompt_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /prompt 指令：在群组触发，将完整的 System Prompt 发送到管理员私聊
    """
    user = update.effective_user
    chat = update.effective_chat

    # 鉴权移至装饰器
    
    if chat.type == ChatType.PRIVATE:
        await update.message.reply_text("💡 请在群组中使用此指令，以预览针对该群组生成的提示词。")
        return

    # 1. 获取配置与摘要
    from core.config_service import config_service
    from core.summary_service import summary_service
    from core.media_service import media_service # 引入用于检测类型
    from utils.prompts import prompt_builder
    from config.settings import settings
    import html

    # 1.1 检测最后的交互模式
    try:
        last_msg_type = await media_service.get_last_user_message_type(chat.id)
        # 简单映射：根据最后一条消息类型来预览 Protocol
        # 注意：这只是为了预览 System Prompt，真实聊天中是根据当次 Payload 动态生成的
        simulated_has_voice = (last_msg_type == "voice")
        simulated_has_image = (last_msg_type == "image")
    except Exception as e:
        logger.warning(f"Failed to detect last message type for {chat.id}: {e}")
        simulated_has_voice = False
        simulated_has_image = False
        last_msg_type = "text (fallback)"

    dynamic_summary_raw = await summary_service.get_summary(chat.id)
    configs = await config_service.get_all_settings()
    soul_prompt = configs.get("system_prompt")
    timezone = configs.get("timezone", "UTC")

    # 2. 组装静态协议 (显式传入 None，使其在第一部分预览中完全不拼装摘要块)
    full_static_prompt = prompt_builder.build_system_prompt(
        soul_prompt=soul_prompt, 
        timezone=timezone, 
        dynamic_summary=None,
        has_voice=simulated_has_voice,
        has_image=simulated_has_image
    )

    # 2.1 获取动态记忆部分 (摘要 + 历史上下文)
    memory_block = prompt_builder.build_memory_block(dynamic_summary_raw)
    
    from core.history_service import history_service
    target_tokens = int(configs.get("history_tokens", settings.HISTORY_WINDOW_TOKENS))
    history_msgs = await history_service.get_token_controlled_context(chat.id, target_tokens=target_tokens)
    
    # 构建动态预览块
    dynamic_preview = memory_block.strip() # 包含长期记忆头
    
    # B. 最近上下文
    dynamic_preview += "\n\n# 最近上下文 (Recent Context)\n"
    if not history_msgs:
        dynamic_preview += "> (No recent history)"
    else:
        import pytz
        try:
            tz = pytz.timezone(timezone)
        except:
            tz = pytz.UTC

        for m in history_msgs:
            if m.timestamp:
                try:
                    ts = m.timestamp.replace(tzinfo=pytz.UTC) if m.timestamp.tzinfo is None else m.timestamp
                    time_str = ts.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S")
                except:
                    time_str = "Time Error"
            else:
                time_str = "Unknown"
            
            msg_id_str = f"MSG {m.message_id}" if m.message_id else "MSG ?"
            msg_type_str = m.message_type.capitalize() if m.message_type else "Text"
            prefix = f"[{msg_id_str}] [{time_str}] [{msg_type_str}] "
            
            content_snippet = m.content[:200] + ('...' if len(m.content) > 200 else '')
            dynamic_preview += f"{prefix}[{m.role.upper()}]: {content_snippet}\n"

    # 3. 格式化页眉
    from datetime import datetime
    import pytz
    try:
        now_str = datetime.now(pytz.timezone(timezone)).strftime("%Y-%m-%d %H:%M:%S")
    except:
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S") + " (UTC)"
    
    header = (
        f"🔍 <b>System Prompt Preview</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"<b>Chat ID:</b> <code>{chat.id}</code>\n"
        f"<b>Chat Name:</b> {chat.title}\n"
        f"<b>Last Msg Type:</b> <code>{str(last_msg_type).upper()}</code>\n"
        f"<b>Generated At:</b> {now_str}\n"
        f"━━━━━━━━━━━━━━━\n\n"
    )

    # 4. 分段发送私聊
    try:
        # 第一部分：静态协议与人设 (如果超长，保留尾部最新的 Protocol 定义)
        safe_static = html.escape(full_static_prompt)
        if len(safe_static) > 3500:
             safe_static = "... (Head Omitted)\n" + safe_static[-3500:]
        content_static = f"{header}<b>[1/2] System Protocol (Static)</b>\n<pre>{safe_static}</pre>"
        
        await context.bot.send_message(user.id, content_static, parse_mode='HTML')
        
        # 第二部分：动态记忆与上下文 (如果是超长，保留摘要，截断中间的旧历史)
        safe_dynamic = html.escape(dynamic_preview)
        if len(safe_dynamic) > 3500:
             # 尝试寻找 "# 最近上下文" 作为分割点
             marker = html.escape("# 最近上下文 (Recent Context)")
             if marker in safe_dynamic:
                 head_part, tail_part = safe_dynamic.split(marker, 1)
                 # 保留摘要头，以及上下文尾部 2000 字符
                 safe_dynamic = f"{head_part}{marker}\n... (Earlier history omitted)\n{tail_part[-2000:]}"
             else:
                 # 兜底截断尾部
                 safe_dynamic = "... (Head Omitted)\n" + safe_dynamic[-3500:]
                 
        content_dynamic = f"<b>[2/2] Memory & Context (Dynamic)</b>\n<pre>{safe_dynamic}</pre>"

        await context.bot.send_message(user.id, content_dynamic, parse_mode='HTML')
        
        await update.message.reply_text("✅ 提示词预览已分段发送。")
    except Exception as e:
        logger.error(f"Failed to send prompt preview: {e}", exc_info=True)
        await update.message.reply_text(f"❌ 预览发送失败。请检查机器人是否已在私聊中启动。")

@require_admin_access
async def debug_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /debug 指令：在私聊中发送最新的系统日志
    """
    user = update.effective_user
    # 鉴权移至装饰器
    
    import os
    log_path = os.path.join("logs", "echogram.log")
    if not os.path.exists(log_path):
        await update.message.reply_text("❌ 未找到日志文件。")
        return

    try:
        with open(log_path, "r", encoding="utf-8") as f:
            # 读取最后 3000 字符
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 3000))
            logs = f.read()

        import html
        safe_logs = html.escape(logs)
        content = f"📜 <b>Recent System Logs</b>\n<pre>{safe_logs}</pre>"
        if len(content) > 4000:
            content = "..." + content[-3950:]

        await context.bot.send_message(user.id, content, parse_mode='HTML')
        if update.effective_chat.type != 'private':
            await update.message.reply_text("✅ 最新日志已发送至您的私聊。")
    except Exception as e:
        logger.error(f"Failed to send debug logs: {e}")
        await update.message.reply_text("❌ 读取日志失败。")

# 注意: add_whitelist 需要在非白名单群组执行，故仅需 Admin 校验，不能用 verify_whitelisted 装饰器
# 因此不加装饰器，保持手动检查
async def add_whitelist_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /add_whitelist 指令：将当前群组加入白名单
    """
    user = update.effective_user
    chat = update.effective_chat
    
    # 鉴权移至装饰器 (Wait, this one does NOT have decorator in original code, but has manual checks. AND the commented out code says it keeps manual check)
    # Actually, in viewed file, line 318 says "注意: add_whitelist 需要在非白名单群组执行... 因此不加装饰器"
    # But wait, lines 360+ in original file show sub_command has decorator.
    # Lines 320 in original file show add_whitelist_command.
    
    if not is_admin(user.id):
        return
    if chat.type == ChatType.PRIVATE:
        await update.message.reply_text("⚠️ 此指令仅限在群组中使用。")
        return

    from core.access_service import access_service
    
    # 记录名称：群组用 title
    description = chat.title
    await access_service.add_whitelist(chat.id, chat.type, description)
    
    await update.message.reply_text(f"✅ 已将本会话 <code>{description}</code> (<code>{chat.id}</code>) 加入白名单。", parse_mode='HTML')

@require_admin_access
async def remove_whitelist_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /remove_whitelist 指令：将当前群组移出白名单
    """
    user = update.effective_user
    chat = update.effective_chat
    
    # 鉴权移至装饰器
    
    if chat.type == ChatType.PRIVATE:
        await update.message.reply_text("⚠️ 此指令仅限在群组中使用。")
        return

    from core.access_service import access_service
    await access_service.remove_whitelist(chat.id)
    
    await update.message.reply_text(f"🗑️ 已将本会话 (<code>{chat.id}</code>) 从白名单中移除。", parse_mode='HTML')

@require_admin_access
async def sub_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /sub 指令：快速添加订阅并绑定到当前群组
    用法: /sub <rss_route> <name>
    """
    user = update.effective_user
    chat = update.effective_chat
    
    # 鉴权移至装饰器
    
    if chat.type == ChatType.PRIVATE:
        await update.message.reply_text("⚠️ 请在群组中使用，以便自动绑定目标群组。私聊请使用 Dashboard。")
        return

    # Args check
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "❌ 用法错误。\n格式: <code>/sub &lt;RSS路由&gt; &lt;名称&gt;</code>\n示例: <code>/sub /telegram/channel/tginfo TG Info</code>",
            parse_mode='HTML'
        )
        return

    route = context.args[0]
    name = " ".join(context.args[1:])

    # Check whitelist first (Duplicates decorator but keeps explicit specific message)
    # Decorator handles secure bail out, manual check here can be removed or kept for "double safety"
    # Actually, decorator handles whitelisting, so we are safe.

    # Add & Bind
    from core.news_push_service import news_push_service
    try:
        # news_push_service.add_subscription handles Creation + Binding (Idempotent)
        success = await news_push_service.add_subscription(route, name, bind_chat_id=chat.id)
        
        if success:
            await update.message.reply_text(
                f"✅ 订阅成功！\n\n<b>源名称:</b> {name}\n<b>路由:</b> <code>{route}</code>\n<b>已绑定:</b> {chat.title}", 
                parse_mode='HTML'
            )
        else:
            await update.message.reply_text("❌ 订阅失败，请检查日志或路由格式。")
    except Exception as e:
        logger.error(f"Sub command failed: {e}")
        await update.message.reply_text(f"❌ 系统错误: {e}")

@require_admin_access
async def push_now_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /push_now 指令：强制触发一次新闻推送检查 (忽略时间/闲置限制)
    """
    user = update.effective_user
    chat = update.effective_chat # Needed for implicit check in wrapper
    
    # 鉴权移至装饰器

    await update.message.reply_text("🚀 正在强制执行 NewsPush 检查...\n(忽略 Active Hours 与 Idle Check)")
    from core.news_push_service import news_push_service
    
    # Force run
    try:
        await news_push_service.run_push_loop(context, force=True)
        await update.message.reply_text("✅ 检查循环执行完毕。请观察群组消息。")
    except Exception as e:
        logger.error(f"Push Now Failed: {e}")
        await update.message.reply_text(f"❌ 执行出错: {e}")


# 简单的内存状态管理 (Key: UUID)
import uuid
PENDING_CONFIRMATIONS = {}


def _merge_new_content_into_chat_xml(old_content: str, new_content: str) -> str:
    """若旧内容为 <chat ...>...</chat>，仅替换标签内文本，保留属性。"""
    text = old_content or ""
    m = re.search(r"<chat(?P<attrs>[^>]*)>.*?</chat>", text, flags=re.DOTALL | re.IGNORECASE)
    if not m:
        return new_content

    attrs = m.group("attrs") or ""
    replacement = f"<chat{attrs}>{new_content}</chat>"
    return re.sub(r"<chat[^>]*>.*?</chat>", replacement, text, count=1, flags=re.DOTALL | re.IGNORECASE)


def _preview_visible_content(raw_content: str) -> str:
    """/preview 展示用：优先显示 <chat> 标签内文本，隐藏标签本体。"""
    text = raw_content or ""
    m = re.search(r"<chat[^>]*>(?P<body>.*?)</chat>", text, flags=re.DOTALL | re.IGNORECASE)
    if m:
        return (m.group("body") or "").strip()
    # 兜底：去掉其他标签，仅展示可读文本
    return re.sub(r"<[^>]+>", "", text, flags=re.DOTALL).strip()

@require_admin_access
async def edit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /edit 指令：修改历史消息
    用法:
    - /edit <ID>, <NewContent>
    - 回复某条消息后：/edit <NewContent>
    """
    user = update.effective_user
    chat = update.effective_chat
    
    # 鉴权移至装饰器

    # 统一使用原始文本解析，避免空格分隔的不稳定行为
    raw_text = (update.message.text or "").strip() if update.message and update.message.text else ""

    # 解析逗号显式分隔格式：/edit <ID>, <NewContent>
    # 支持英文逗号与中文逗号
    # 例如：/edit 1984, 这是要改的新内容 或 /edit 1984，这是要改的新内容
    target_id = None
    new_content = ""
    body = ""
    if raw_text and raw_text.lower().startswith("/edit"):
        body = raw_text.split(maxsplit=1)
        body = body[1].strip() if len(body) > 1 else ""
        delimiter = None
        if "," in body:
            delimiter = ","
        elif "，" in body:
            delimiter = "，"

        if delimiter:
            left, right = body.split(delimiter, 1)
            left = left.strip()
            right = right.strip()
            if left:
                try:
                    target_id = int(left)
                    new_content = right
                except ValueError:
                    # 若当前是“回复模式”，允许正文里出现逗号而不要求前置 ID
                    if update.message.reply_to_message:
                        target_id = int(update.message.reply_to_message.message_id)
                        new_content = body.strip()
                    else:
                        await update.message.reply_text("❌ 逗号前必须是数字 ID。示例：<code>/edit 1984, 新内容</code>", parse_mode='HTML')
                        return

    # 若逗号格式没命中，只允许“回复模式”
    if target_id is None:
        if update.message.reply_to_message:
            target_id = int(update.message.reply_to_message.message_id)
            # 更稳健地提取 /edit 后正文，兼容 /edit@BotName 与非常规空白
            m = re.match(r"^/edit(?:@\w+)?\s*(?P<body>[\s\S]*)$", raw_text, flags=re.IGNORECASE)
            new_content = (m.group("body") if m else body).strip()
        else:
            await update.message.reply_text(
                "❌ 请使用显式分隔格式：<code>/edit &lt;ID&gt;, &lt;新内容&gt;</code>\n"
                "或先回复目标消息再发送 <code>/edit &lt;新内容&gt;</code>",
                parse_mode='HTML'
            )
            return

    if not new_content:
        await update.message.reply_text("❌ 新内容不能为空。")
        return

    # 优先尝试作为 DB ID (Global ID) 获取对象
    msg_obj = await history_service.get_message_by_db_id(target_id, chat_id=chat.id)
    if not msg_obj:
        msg_obj = await history_service.get_message(chat.id, target_id)

    if not msg_obj:
        await update.message.reply_text(f"❌ 未找到 ID 为 `{target_id}` 的消息 (在此会话中)。", parse_mode='Markdown')
        return

    # Strict Check: Can only edit Bot messages
    if msg_obj.role == "user":
        await update.message.reply_text("❌ 只能修改 Bot 发送的消息，无法修改用户的发言。", parse_mode='Markdown')
        return

    # Check Archival Status (Cannot edit archived messages)
    from core.summary_service import summary_service
    status = await summary_service.get_status(chat.id)
    last_archived_id = status["last_id"]
    
    if msg_obj.id <= last_archived_id:
        await update.message.reply_text(
            f"❌ 消息已归档 (ID {msg_obj.id} <= {last_archived_id})，无法修改。\n"
            "因为该消息已被压缩进长期记忆摘要，修改源文件会导致记忆不一致。",
            parse_mode='HTML'
        )
        return

    # Generate Confirmation
    confirm_id = str(uuid.uuid4())[:8]
    PENDING_CONFIRMATIONS[confirm_id] = {
        "type": "edit",
        "chat_id": chat.id,
        "user_id": user.id,
        "target_db_id": msg_obj.id,
        "target_msg_id": msg_obj.message_id, # for TG edit
        "message_type": msg_obj.message_type or "text", # Pass type
        "is_bot_msg": (msg_obj.role == "assistant" or str(msg_obj.role).lower() == "bot"), # approximate check
        "old_content": msg_obj.content,
        "new_content": new_content,
        "timestamp": 0 # TODO: cleanup
    }
    
    import html
    old_preview = html.escape(msg_obj.content[:200]) + "..." if len(msg_obj.content) > 200 else html.escape(msg_obj.content)
    new_preview = html.escape(new_content[:200]) + "..." if len(new_content) > 200 else html.escape(new_content)
    
    type_warn = ""
    if msg_obj.message_type == "voice":
        type_warn = "\n⚠️ <b>语音消息:</b> 将修改其附言 (Caption)，同时修正数据库记录。\n"

    text = (
        f"✏️ <b>确认修改消息 [{target_id}]？</b>\n{type_warn}\n"
        f"🔻 <b>原文</b>:\n<pre>{old_preview}</pre>\n\n"
        f"🔺 <b>新文</b>:\n<pre>{new_preview}</pre>"
    )
    
    keyboard = [
        [
            InlineKeyboardButton("✅ 确认修改", callback_data=f"admin:confirm:{confirm_id}"),
            InlineKeyboardButton("❌ 取消", callback_data=f"admin:cancel:{confirm_id}")
        ]
    ]
    
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')


@require_admin_access
async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /del 指令：删除历史消息
    用法: 
    - /del <ID> (单个)
    - /del <ID> <ID> ... (空格分隔)
    - /del <ID> ... <Start>-<End> ... (混合范围)
    - 回复某条消息并发送 /del
    """
    user = update.effective_user
    chat = update.effective_chat
    
    # 鉴权移至装饰器

    target_ids = set()
    
    # 场景 1: 回复引用 (优先处理)
    if update.message.reply_to_message:
        target_ids.add(update.message.reply_to_message.message_id)

    # 场景 2: 参数解析
    if context.args:
        raw_args = " ".join(context.args)
        normalized = raw_args.replace(" ", ",") 
        parts = [p.strip() for p in normalized.split(",") if p.strip()]
        
        for part in parts:
            if "-" in part:
                try:
                    start_s, end_s = part.split("-", 1)
                    start, end = int(start_s), int(end_s)
                    if start > end: start, end = end, start
                    if (end - start) > 100:
                        await update.message.reply_text(f"⚠️ 范围过大 ({part})，单次限制 100 条。已跳过。")
                        continue
                    for i in range(start, end + 1):
                        target_ids.add(i)
                except ValueError:
                    continue
            else:
                try:
                    clean_part = part.replace("/", "")
                    if not clean_part: continue
                    target_ids.add(int(clean_part))
                except ValueError:
                    continue

    if not target_ids:
        await update.message.reply_text("❌ 用法: `/del <ID> [ID] [Start-End]` (空格分隔)", parse_mode='Markdown')
        return

# --- Helper for Delete Confirmation UI ---
def _render_delete_view(confirm_id: str, page: int = 0):
    """
    Render text and keyboard for a specific page of delete confirmation.
    Returns: (text, reply_markup) or None if state invalid
    """
    state = PENDING_CONFIRMATIONS.get(confirm_id)
    if not state: return None, None
    
    targets = state["targets"]
    total_items = len(targets)
    items_per_page = 10
    total_pages = (total_items + items_per_page - 1) // items_per_page
    
    # Ensure page is valid
    if page < 0: page = 0
    if page >= total_pages: page = total_pages - 1
    
    # Slice items
    start = page * items_per_page
    end = start + items_per_page
    page_items = targets[start:end]
    
    # Text Body
    preview_lines = []
    for item in page_items:
        # Format: • 101|102 [user]: content...
        preview_lines.append(f"• <code>{item['db_id']}|{item['msg_id']}</code> [{item['role']}]: {item['preview']}")
        
    preview_text = "\n".join(preview_lines)
    
    header = f"🗑️ <b>确认删除消息 ({total_items}条)</b>"
    if total_pages > 1:
        header += f" [Page {page+1}/{total_pages}]"
        
    text = (
        f"{header}\n\n"
        f"{preview_text}\n\n"
        f"⚠️ 操作将物理删除数据库记录与群消息。"
    )
    
    # Keyboard
    keyboard = []
    
    # Navigation Row (Only if needed)
    if total_pages > 1:
        nav_row = []
        # Previous
        if page > 0:
            nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"admin:page:{confirm_id}:{page-1}"))
        else:
            nav_row.append(InlineKeyboardButton("Wait", callback_data="admin:ignore")) # Placeholder
            
        # Page Indicator (Middle)
        nav_row.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="admin:ignore"))
        
        # Next
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"admin:page:{confirm_id}:{page+1}"))
        else:
            nav_row.append(InlineKeyboardButton("End", callback_data="admin:ignore")) # Placeholder
            
        keyboard.append(nav_row)
        
    # Action Row
    action_row = [
        InlineKeyboardButton(f"✅ 确认全部 ({total_items})", callback_data=f"admin:confirm:{confirm_id}"),
        InlineKeyboardButton("❌ 取消", callback_data=f"admin:cancel:{confirm_id}")
    ]
    keyboard.append(action_row)
    
    return text, InlineKeyboardMarkup(keyboard)


def _render_preview_view(confirm_id: str, page: int = 0):
    """
    Render text and keyboard for previewing DB message content.
    Returns: (text, reply_markup) or (None, None) if state invalid.
    """
    state = PENDING_CONFIRMATIONS.get(confirm_id)
    if not state:
        return None, None

    targets = state.get("targets", [])
    total_items = len(targets)
    if total_items <= 0:
        return "⚠️ 无可预览消息。", InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ 关闭", callback_data=f"admin:cancel:{confirm_id}")]
        ])

    items_per_page = 2
    total_pages = (total_items + items_per_page - 1) // items_per_page

    if page < 0:
        page = 0
    if page >= total_pages:
        page = total_pages - 1

    start = page * items_per_page
    end = start + items_per_page
    page_items = targets[start:end]

    blocks = []
    for item in page_items:
        content = item.get("content", "") or ""
        if len(content) > 1200:
            content = content[:1200] + "\n... (truncated)"
        blocks.append(
            f"• <b>{item['db_id']}|{item['msg_id']}</b> "
            f"[{item['role']}/{item['msg_type']}]\n"
            f"<pre>{content}</pre>"
        )

    body = "\n\n".join(blocks)
    header = f"🔎 <b>数据库消息预览</b> ({total_items}条)"
    if total_pages > 1:
        header += f" [Page {page+1}/{total_pages}]"

    text = (
        f"{header}\n"
        f"{body}"
    )

    keyboard = []
    if total_pages > 1:
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"admin:page:{confirm_id}:{page-1}"))
        else:
            nav_row.append(InlineKeyboardButton("Wait", callback_data="admin:ignore"))

        nav_row.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="admin:ignore"))

        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"admin:page:{confirm_id}:{page+1}"))
        else:
            nav_row.append(InlineKeyboardButton("End", callback_data="admin:ignore"))
        keyboard.append(nav_row)

    keyboard.append([InlineKeyboardButton("❌ 关闭", callback_data=f"admin:cancel:{confirm_id}")])

    return text, InlineKeyboardMarkup(keyboard)

@require_admin_access
async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /del 指令：删除历史消息
    用法: 
    - /del <ID> (单个)
    - /del <ID> <ID> ... (空格分隔)
    - /del <ID> ... <Start>-<End> ... (混合范围)
    - 回复某条消息并发送 /del
    """
    user = update.effective_user
    chat = update.effective_chat
    
    # 鉴权移至装饰器

    target_ids = set()
    
    # 场景 1: 回复引用 (优先处理)
    if update.message.reply_to_message:
        target_ids.add(update.message.reply_to_message.message_id)

    # 场景 2: 参数解析
    if context.args:
        raw_args = " ".join(context.args)
        normalized = raw_args.replace(" ", ",") 
        parts = [p.strip() for p in normalized.split(",") if p.strip()]
        
        for part in parts:
            if "-" in part:
                try:
                    start_s, end_s = part.split("-", 1)
                    start, end = int(start_s), int(end_s)
                    if start > end: start, end = end, start
                    if (end - start) > 100:
                        await update.message.reply_text(f"⚠️ 范围过大 ({part})，单次限制 100 条。已跳过。")
                        continue
                    for i in range(start, end + 1):
                        target_ids.add(i)
                except ValueError:
                    continue
            else:
                try:
                    clean_part = part.replace("/", "")
                    if not clean_part: continue
                    target_ids.add(int(clean_part))
                except ValueError:
                    continue

    if not target_ids:
        await update.message.reply_text("❌ 用法: `/del <ID> [ID] [Start-End]` (空格分隔)", parse_mode='Markdown')
        return

    # Preview Logic (Fetch & Validate)
    sorted_ids = sorted(list(target_ids))
    valid_targets = [] # List of {"db_id": int, "msg_id": int, "role": str, "preview": str}
    
    for tid in sorted_ids:
        # Resolve ID
        msg_obj = await history_service.get_message_by_db_id(tid, chat_id=chat.id)
        if not msg_obj:
            msg_obj = await history_service.get_message(chat.id, tid)
        
        if msg_obj:
            import html
            content_snippet = html.escape(msg_obj.content[:50].replace("\n", " "))
            valid_targets.append({
                "db_id": msg_obj.id, 
                "msg_id": msg_obj.message_id,
                "role": msg_obj.role,
                "preview": content_snippet
            })
        else:
            # Skip invalid IDs (No Blind Delete)
            continue
            
    # Check Archival Rules
    from core.summary_service import summary_service
    status = await summary_service.get_status(chat.id)
    last_archived_id = status["last_id"]
    
    final_targets = []
    skipped_archived_count = 0
    
    for t in valid_targets:
        if t["db_id"] <= last_archived_id:
            skipped_archived_count += 1
        else:
            final_targets.append(t)
    
    valid_targets = final_targets
    
    if not valid_targets:
        if skipped_archived_count > 0:
            await update.message.reply_text(f"⚠️ 所有选中消息均已归档 (Archived)，为了保持记忆完整性，系统禁止删除已总结的历史。")
        else:
            await update.message.reply_text("⚠️ 未找到任何匹配的消息记录 (所有 ID 均无效)。")
        return
    
    warning_suffix = ""
    if skipped_archived_count > 0:
        warning_suffix = f"\n\n🚫 <b>已自动排除 {skipped_archived_count} 条归档消息</b> (只能删除流动窗口内的消息)"

    # Init State
    confirm_id = str(uuid.uuid4())[:8]
    PENDING_CONFIRMATIONS[confirm_id] = {
        "type": "delete",
        "chat_id": chat.id,
        "user_id": user.id,
        "targets": valid_targets,
        "timestamp": 0
    }

    # Render Page 0
    text, markup = _render_delete_view(confirm_id, page=0)
    
    if warning_suffix:
        # Append warning to first page text
        text += warning_suffix
        
    await update.message.reply_text(text, reply_markup=markup, parse_mode='HTML')


@require_admin_access
async def preview_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /preview 指令：预览数据库中的消息原文
    用法:
    - /preview <ID> (单个)
    - /preview <ID> <ID> ... (空格分隔)
    - /preview <ID> ... <Start>-<End> ... (混合范围)
    - 回复某条消息并发送 /preview
    """
    user = update.effective_user
    chat = update.effective_chat

    if chat.type == ChatType.PRIVATE:
        await update.message.reply_text("⚠️ 请在群组中使用 /preview。")
        return

    target_ids = set()

    # 场景 1: 回复引用
    if update.message.reply_to_message:
        target_ids.add(update.message.reply_to_message.message_id)

    # 场景 2: 参数解析（与 /del 语法一致）
    if context.args:
        raw_args = " ".join(context.args)
        normalized = raw_args.replace(" ", ",")
        parts = [p.strip() for p in normalized.split(",") if p.strip()]

        for part in parts:
            if "-" in part:
                try:
                    start_s, end_s = part.split("-", 1)
                    start, end = int(start_s), int(end_s)
                    if start > end:
                        start, end = end, start
                    if (end - start) > 100:
                        await update.message.reply_text(f"⚠️ 范围过大 ({part})，单次限制 100 条。已跳过。")
                        continue
                    for i in range(start, end + 1):
                        target_ids.add(i)
                except ValueError:
                    continue
            else:
                try:
                    clean_part = part.replace("/", "")
                    if not clean_part:
                        continue
                    target_ids.add(int(clean_part))
                except ValueError:
                    continue

    if not target_ids:
        await update.message.reply_text("❌ 用法: `/preview <ID> [ID] [Start-End]` (空格分隔)", parse_mode='Markdown')
        return

    sorted_ids = sorted(list(target_ids))
    targets = []

    import html
    for tid in sorted_ids:
        msg_obj = await history_service.get_message_by_db_id(tid, chat_id=chat.id)
        if not msg_obj:
            msg_obj = await history_service.get_message(chat.id, tid)

        if not msg_obj:
            continue

        targets.append({
            "db_id": msg_obj.id,
            "msg_id": msg_obj.message_id,
            "role": msg_obj.role,
            "msg_type": msg_obj.message_type or "text",
            "content": html.escape(_preview_visible_content(msg_obj.content or ""))
        })

    if not targets:
        await update.message.reply_text("⚠️ 未找到任何匹配的消息记录 (所有 ID 均无效)。")
        return

    confirm_id = str(uuid.uuid4())[:8]
    PENDING_CONFIRMATIONS[confirm_id] = {
        "type": "preview",
        "chat_id": chat.id,
        "user_id": user.id,
        "targets": targets,
        "timestamp": 0
    }

    text, markup = _render_preview_view(confirm_id, page=0)
    await update.message.reply_text(text, reply_markup=markup, parse_mode='HTML')


async def admin_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    处理 /del 和 /edit 的确认回调
    Data: admin:<action>:<uuid>[:arg]
    """
    query = update.callback_query
    user = update.effective_user
    
    # Check data pattern
    data = query.data
    if not data.startswith("admin:"):
        return

    parts = data.split(":")
    action = parts[1] # confirm, cancel, page, ignore
    
    if action == "ignore":
        await query.answer()
        return
        
    confirm_id = parts[2]
    
    # Retrieve State
    state = PENDING_CONFIRMATIONS.get(confirm_id)
    if not state:
        await query.answer("⚠️ 操作已过期", show_alert=True)
        try:
            await query.edit_message_text("❌ 操作已过期 (State Lost)")
        except:
            pass
        return

    # Verify User
    if state["user_id"] != user.id:
        await query.answer("❌ 只能由指令发起人操作", show_alert=True)
        return

    # Handle Paging (No state cleanup yet)
    if action == "page":
        new_page = int(parts[3])
        if state.get("type") == "preview":
            text, markup = _render_preview_view(confirm_id, page=new_page)
        else:
            text, markup = _render_delete_view(confirm_id, page=new_page)
        if text:
            await query.answer() # Ack
            try:
                await query.edit_message_text(text, reply_markup=markup, parse_mode='HTML')
            except Exception as e:
                # Message not modified error is common if clicking same page logic
                pass
        return

    # Handle Final Actions (Confirm/Cancel) --> Cleanup State
    del PENDING_CONFIRMATIONS[confirm_id]

    if action == "cancel":
        await query.answer("已取消")
        if state.get("type") == "preview":
            await query.edit_message_text("✅ 预览已关闭")
        else:
            await query.edit_message_text(f"❌ 操作已取消 (By {user.first_name})")
        return

    await query.answer("Processing...")
    
    # Execute Action
    if state["type"] == "delete":
        targets = state["targets"]
        success_db = 0
        success_tg = 0
        fail_count = 0
        
        chat_id = state["chat_id"]
        
        for t in targets:
            db_id = t["db_id"]
            msg_id = t["msg_id"]
            
            # 1. TG Delete
            tg_ok = False
            if msg_id:
                try:
                    await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
                    success_tg += 1
                    tg_ok = True
                except Exception:
                    pass
            
            # 2. DB Delete
            db_ok = False
            # Try by DB ID first
            if await history_service.delete_message_by_db_id(db_id, chat_id=chat_id):
                success_db += 1
                db_ok = True
            # Fallback by Msg ID
            elif msg_id and await history_service.delete_message(chat_id, msg_id):
                success_db += 1
                db_ok = True
            
            if not tg_ok and not db_ok:
                fail_count += 1
        
        report = (
            f"🗑️ <b>删除完成</b>\n"
            f"🧠 记忆清除: {success_db} 条\n"
            f"💥 物理粉碎: {success_tg} 条"
        )
        if fail_count > 0:
            report += f"\n⚠️ 失败: {fail_count} 条"
            
        await query.edit_message_text(report, parse_mode='HTML')

    elif state["type"] == "edit":
        # Edit logic unchanged
        chat_id = state["chat_id"]
        db_id = state["target_db_id"]
        msg_id = state["target_msg_id"]
        new_content = state["new_content"]
        old_content = state.get("old_content", "")
        msg_type = state.get("message_type", "text") # Get type

        # DB 保留原始 XML 结构（仅替换 <chat> 内文本）
        db_content = _merge_new_content_into_chat_xml(old_content, new_content)
        
        # 1. DB Update
        db_ok = await history_service.update_message_content_by_db_id(db_id, db_content, chat_id=chat_id)
        
        if not db_ok:
            await query.edit_message_text("❌ 数据库更新失败 (可能已被删除)")
            return

        # 2. TG Update (Skip if voice)
        tg_ok = False
        fail_reason = ""
        
        if msg_type == "voice":
            try:
                # Update Caption (Limit 1024 chars for Caption)
                safe_caption = new_content[:1024]
                await context.bot.edit_message_caption(chat_id=chat_id, message_id=msg_id, caption=safe_caption)
                tg_ok = True
                tg_skip_msg = ""
            except Exception as e:
                fail_reason = str(e)
                if "Message is not modified" in str(e):
                    tg_ok = True
                    
        elif msg_id:
            try:
                await context.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=new_content)
                tg_ok = True
                tg_skip_msg = ""
            except Exception as e:
                fail_reason = str(e)
                if "Message is not modified" in str(e):
                    tg_ok = True
        else:
            tg_skip_msg = " (无 MsgID)"
        
        if tg_ok:
            if msg_type == "voice":
                await query.edit_message_text(f"✅ <b>完美同步</b>: 听写已存入数据库，语音附言已更新。", parse_mode='HTML')
            else:
                await query.edit_message_text(f"✅ <b>完美同步</b>: 记忆与消息均已修正。", parse_mode='HTML')
        else:
            if msg_type == "voice":
                await query.edit_message_text(f"✅ <b>听写已修正</b> (附言更新失败: {fail_reason})", parse_mode='HTML')
            else:
                await query.edit_message_text(f"✅ <b>记忆已修正</b> (物理消息未变: {fail_reason})", parse_mode='HTML')

    elif state["type"] == "preview":
        await query.edit_message_text("✅ 预览已关闭")


