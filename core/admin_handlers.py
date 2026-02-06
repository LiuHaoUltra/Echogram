import os
from telegram import Update, constants
from telegram.ext import ContextTypes
from core.history_service import history_service
from core.secure import is_admin
from utils.logger import logger

async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /reset 指令：清空当前对话的历史记忆
    """
    user = update.effective_user
    chat = update.effective_chat
    
    # 鉴权：非管理员完全静默
    if not is_admin(user.id):
        return

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

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /stats 指令：查看当前会话的记忆状态
    """
    user = update.effective_user
    chat = update.effective_chat
    
    if not is_admin(user.id):
        return
    if chat.type == constants.ChatType.PRIVATE:
        await update.message.reply_text("📊 请在群组中使用此指令查看统计信息。")
        return

    # 获取配置
    from core.config_service import config_service
    from config.settings import settings
    
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

async def prompt_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /prompt 指令：在群组触发，将完整的 System Prompt 发送到管理员私聊
    """
    user = update.effective_user
    chat = update.effective_chat

    if not is_admin(user.id):
        return
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

async def debug_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /debug 指令：在私聊中发送最新的系统日志
    """
    user = update.effective_user
    if not is_admin(user.id):
        return

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

async def remove_whitelist_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /remove_whitelist 指令：将当前群组移出白名单
    """
    user = update.effective_user
    chat = update.effective_chat
    
    if not is_admin(user.id):
        return
    if chat.type == constants.ChatType.PRIVATE:
        await update.message.reply_text("⚠️ 此指令仅限在群组中使用。")
        return

    from core.access_service import access_service
    await access_service.remove_whitelist(chat.id)
    
    await update.message.reply_text(f"🗑️ 已将本会话 (<code>{chat.id}</code>) 从白名单中移除。", parse_mode='HTML')

async def sub_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /sub 指令：快速添加订阅并绑定到当前群组
    用法: /sub <rss_route> <name>
    """
    user = update.effective_user
    chat = update.effective_chat
    
    if not is_admin(user.id):
        return
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

    # Check whitelist first
    from core.access_service import access_service
    if not await access_service.is_chat_whitelisted(chat.id):
        await update.message.reply_text("⚠️ 当前群组未在白名单中。请先发送 /add_whitelist 添加。", parse_mode='HTML')
        return

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

async def push_now_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /push_now 指令：强制触发一次新闻推送检查 (忽略时间/闲置限制)
    """
    user = update.effective_user
    if not is_admin(user.id): return

    await update.message.reply_text("🚀 正在强制执行 NewsPush 检查...\n(忽略 Active Hours 与 Idle Check)")
    from core.news_push_service import news_push_service
    
    # Force run
    try:
        await news_push_service.run_push_loop(context, force=True)
        await update.message.reply_text("✅ 检查循环执行完毕。请观察群组消息。")
    except Exception as e:
        logger.error(f"Push Now Failed: {e}")
    except Exception as e:
        logger.error(f"Push Now Failed: {e}")
        await update.message.reply_text(f"❌ 执行出错: {e}")

async def rebuild_rag_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /rebuild_rag 指令：清除当前会话的 RAG 索引并触发重新生成 (保留历史记录)
    """
    user = update.effective_user
    chat = update.effective_chat
    
    if not is_admin(user.id): return
    if chat.type == constants.ChatType.PRIVATE:
        await update.message.reply_text("⚠️ 请在群组中使用。")
        return

    from core.chat_engine import CHAT_LOCKS
    from core.rag_service import rag_service
    
    msg = await update.message.reply_text("⏳ 正在清除 RAG 索引...")
    
    try:
        async with CHAT_LOCKS[chat.id]:
            await rag_service.clear_chat_vectors(chat.id)
            
        # 触发一次后台同步 (Optional, or just wait for scheduled)
        # 既然是手动指令，最好立即给点反馈，但在后台运行
        asyncio.create_task(rag_service.run_background_sync())
        
        await context.bot.edit_message_text(
            chat_id=chat.id,
            message_id=msg.message_id,
            text="✅ <b>索引已清除 & 重建任务已排队</b>\n\n后台正在重新扫描并降噪历史消息。请过几分钟使用 /stats 查看进度。",
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"Rebuild RAG failed: {e}")
        await context.bot.edit_message_text(
            chat_id=chat.id,
            message_id=msg.message_id,
            text=f"❌ 重建失败: {e}"
        )
