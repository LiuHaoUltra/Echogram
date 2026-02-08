from telegram import Update, constants
from telegram.ext import ContextTypes
from core.history_service import history_service
from core.secure import is_admin, require_admin_access
from utils.logger import logger

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
    if chat.type == constants.ChatType.PRIVATE:
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
    
    if chat.type == constants.ChatType.PRIVATE:
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
        f"🕒 Last Summary: {time_str}"
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
    
    if chat.type == constants.ChatType.PRIVATE:
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
    
    if not is_admin(user.id):
        return
    if chat.type == constants.ChatType.PRIVATE:
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
    
    if chat.type == constants.ChatType.PRIVATE:
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
    
    if chat.type == constants.ChatType.PRIVATE:
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

@require_admin_access
async def edit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /edit 指令：修改历史消息
    用法: /edit <ID> <NewContent>
    ID 优先尝试 DB ID，其次 Message ID
    """
    user = update.effective_user
    chat = update.effective_chat
    
    # 鉴权移至装饰器

    if not context.args or len(context.args) < 2:
        await update.message.reply_text("❌ 用法: `/edit <ID> <新内容>`", parse_mode='Markdown')
        return

    target_id_str = context.args[0]
    new_content = " ".join(context.args[1:])
    
    try:
        target_id = int(target_id_str)
    except ValueError:
        await update.message.reply_text("❌ ID 必须是数字")
        return

    # 优先尝试作为 DB ID (Global ID) 获取对象
    msg_obj = await history_service.get_message_by_db_id(target_id, chat_id=chat.id)
    if not msg_obj:
        msg_obj = await history_service.get_message(chat.id, target_id)

    if not msg_obj:
        await update.message.reply_text(f"❌ 未找到 ID 为 `{target_id}` 的消息 (在此会话中)。", parse_mode='Markdown')
        return

    # 1. Update DB
    db_success = await history_service.update_message_content_by_db_id(msg_obj.id, new_content, chat_id=chat.id)
    
    if not db_success:
        await update.message.reply_text(f"❌ 数据库更新失败 (ID: {target_id})。", parse_mode='Markdown')
        return

    # 2. Try Update TG Message (Best Effort)
    tg_success = False
    fail_reason = ""
    if msg_obj.message_id:
        try:
            await context.bot.edit_message_text(chat_id=chat.id, message_id=msg_obj.message_id, text=new_content)
            tg_success = True
        except Exception as e:
            # Expected errors: Message can't be edited (User msg), Message not modified, etc.
            fail_reason = str(e)
            if "Message is not modified" in str(e):
                tg_success = True # Treat as success if content is same
            
    if tg_success:
        await update.message.reply_text(f"✅ <b>完美同步</b>: 记忆与消息均已修正。", parse_mode='HTML')
    else:
        # Check if it was a user message (which we can't edit)
        is_user_msg = (msg_obj.role == "user")
        explanation = "(无法修改用户消息)" if is_user_msg else f"(API Error: {fail_reason})"
        await update.message.reply_text(f"✅ <b>记忆已修正</b> {explanation}\n⚠️ 物理消息未变。", parse_mode='HTML')

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
        
        # 如果同时带了参数，也一并处理
        # e.g. reply + "/del 123 124" -> delete reply AND 123 AND 124

    # 场景 2: 参数解析 (支持 100-105, 107 108, 109,110 混合写法)
    if context.args:
        # 将所有参数视为一个长字符串，统一替换分隔符为逗号
        raw_args = " ".join(context.args)
        # 把 / 和 空格 都替换为 , (保留逗号兼容性，移除斜杠支持以免歧义)
        normalized = raw_args.replace(" ", ",") # Just convert space to comma for splitting
        
        parts = [p.strip() for p in normalized.split(",") if p.strip()]
        
        for part in parts:
            # Range: 100-105
            if "-" in part:
                try:
                    start_s, end_s = part.split("-", 1)
                    start, end = int(start_s), int(end_s)
                    if start > end: start, end = end, start # Swap if reversed
                    # 限制一次删除数量以防误操作 (e.g. 1-10000)
                    if (end - start) > 100:
                        await update.message.reply_text(f"⚠️ 范围过大 ({part})，单次限制 100 条。已跳过。")
                        continue
                    for i in range(start, end + 1):
                        target_ids.add(i)
                except ValueError:
                    continue # Ignore format error
            # Single: 100
            else:
                try:
                    # 移除可能误入的 slash (虽然已经不作为分隔符处理)
                    clean_part = part.replace("/", "")
                    if not clean_part: continue
                    target_ids.add(int(clean_part))
                except ValueError:
                    continue

    if not target_ids:
        await update.message.reply_text("❌ 用法: `/del <ID> [ID] [Start-End]` (空格分隔)", parse_mode='Markdown')
        return

    # 执行删除
    # 从集合转为排序列表，方便阅读日志
    sorted_ids = sorted(list(target_ids))
    success_db_count = 0
    success_tg_count = 0
    fail_count = 0
    
    for tid in sorted_ids:
        # Step 1: Resolve to Message Object (Try as DB ID, then as TG Message ID)
        msg_obj = await history_service.get_message_by_db_id(tid, chat_id=chat.id)
        
        # 如果不是 DB ID，尝试作为 TG MSG ID
        if not msg_obj:
            msg_obj = await history_service.get_message(chat.id, tid)

        # Step 2: Delete from Telegram (Physical Delete)
        # 只要找到了 Message ID，就尝试物理删除
        # (即使用户输入的是 DB ID，我们也能通过 msg_obj.message_id 找到对应的 TG ID)
        tg_delete_ok = False
        if msg_obj and msg_obj.message_id:
             try:
                 await context.bot.delete_message(chat_id=chat.id, message_id=msg_obj.message_id)
                 tg_delete_ok = True
                 success_tg_count += 1
             except Exception as e:
                 # 常见错误: Message to delete not found, Message can't be deleted (too old/no permission)
                 logger.warning(f"Failed to delete TG message {msg_obj.message_id}: {e}")
        elif not msg_obj and tid > 0:
            # 即使 DB 里没有，也尝试盲删 TG ID (用户可能就是想删 TG 消息)
            # 但前提是我们确定它极有可能是个 TG ID (tid)
             try:
                 await context.bot.delete_message(chat_id=chat.id, message_id=tid)
                 tg_delete_ok = True
                 success_tg_count += 1
             except Exception:
                 pass
        
        # Step 3: Delete from DB (Memory Delete)
        db_delete_ok = False
        if msg_obj:
             # 有对象，用 DB ID 删最稳
             if await history_service.delete_message_by_db_id(msg_obj.id, chat_id=chat.id):
                 db_delete_ok = True
                 success_db_count += 1
        else:
             # 无对象，尝试作为 DB IDBlind Delete
             if await history_service.delete_message_by_db_id(tid, chat_id=chat.id):
                 db_delete_ok = True
                 success_db_count += 1
             # 再尝试 Msg ID Blind Delete
             elif await history_service.delete_message(chat.id, tid):
                 db_delete_ok = True
                 success_db_count += 1
        
        if not db_delete_ok and not tg_delete_ok:
            fail_count += 1

    msg = f"🗑️ <b>删除报告</b>\n"
    msg += f"🧠 记忆清除: {success_db_count} 条\n"
    msg += f"💥 物理粉碎: {success_tg_count} 条\n"
    
    if fail_count > 0:
        msg += f"⚠️ 未找到/失败: {fail_count} 条\n"
    
    # 如果全失败
    if success_db_count == 0 and success_tg_count == 0 and fail_count > 0:
        msg += "\n(未在数据库或群组中找到指定 ID)"

    await update.message.reply_text(msg, parse_mode='HTML')


