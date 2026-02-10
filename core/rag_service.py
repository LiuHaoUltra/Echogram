import re
import asyncio
import json
import time
from typing import List, Dict, Any, Optional
from sqlalchemy import select, text, and_, bindparam
from openai import AsyncOpenAI
from config.settings import settings
from config.database import get_db_session
from core.config_service import config_service
from models.history import History
from models.rag_status import RagStatus
from utils.logger import logger
import html

class RagService:
    # 默认配置常量
    DEFAULT_SIMILARITY_THRESHOLD = 0.6
    DEFAULT_TOP_K = 5
    SYNC_COOLDOWN_SECONDS = 60 # 每 2 分钟触发主循环，每 1 分钟允许单个 Chat 重爬

    def __init__(self):
        self._client = None
        self._current_api_key = None
        self._current_base_url = None
        self._sync_cooldowns: Dict[int, float] = {}  # chat_id -> last_failure_time

    def _etl_debug(self, msg: str):
        """RAG ETL 调试日志（默认关闭，避免日志膨胀）。"""
        if settings.RAG_VERBOSE_LOG:
            logger.info(msg)

    async def _notify_admin(self, text: str):
        """发送私信给管理员 (内部调试/透明化使用)"""
        if not settings.RAG_NOTIFY_ADMIN:
            return
        from core.bot import bot
        if bot and settings.ADMIN_USER_ID:
            try:
                # 尽量保持静默，如果报错也不阻塞主流程
                await bot.send_message(
                    chat_id=settings.ADMIN_USER_ID,
                    text=text,
                    parse_mode="HTML",
                    disable_web_page_preview=True
                )
            except Exception as e:
                logger.error(f"ETL Notify Admin failed: {e}")
    
    async def _get_client(self):
        """获取或初始化 OpenAI Client (支持动态配置更新)"""
        configs = await config_service.get_all_settings()
        api_key = configs.get("api_key")
        base_url = configs.get("api_base_url")
        
        if not api_key:
             raise ValueError("API Key not configured")

        # 检查配置是否变更
        if (not self._client or 
            api_key != self._current_api_key or 
            base_url != self._current_base_url):
            
            self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
            self._current_api_key = api_key
            self._current_base_url = base_url
            # logger.info("RAG Client (re)initialized with new config.")
            
        return self._client

    async def _get_summary_model(self):
        """获取配置的摘要/清洗模型"""
        configs = await config_service.get_all_settings()
        # 优先使用 summary_model_name, 降级使用 model_name
        model = configs.get("summary_model_name") or configs.get("model_name")
        return model

    async def denoise_interaction(self, user_content: str, ai_content: str) -> str:
        """
        [ETL Phase 1] 使用 LLM 进行语义降噪
        将 User+AI 的完整对话轮次转化为高密度的客观事实。
        """
        model_name = await self._get_summary_model()
        if not model_name:
            return f"User asked: {user_content}\nAI answered: {ai_content}"

        sys_prompt = (
            "你是一名 RAG 知识库构建专家。你的任务是将用户的提问和 AI 的回复清洗为一条“高密度”的事实记录。\n"
            "规则：\n"
            "1. **提取核心**：提取用户遇到的具体问题（报错信息、代码上下文）和 AI 给出的关键建议。\n"
            "2. **去除噪音**：彻底删除所有寒暄（“你好”、“谢谢”）、情绪词（“烦死了”）、口语废话（“那个...”）。\n"
            "3. **指代消歧**：如果用户说“它挂了”，请根据上下文（如果有）或直接保留原词但尝试补充背景。\n"
            "4. **格式**：输出为第三人称陈述句。例如：“用户询问 Docker 启动失败 (Exit 137)。AI 解释为 OOM 并建议增加 Swap。”\n"
            "5. **只输出结果**，不要包含任何前缀或解释。"
        )

        user_prompt = f"User Input:\n{user_content}\n\nAI Response:\n{ai_content}"

        try:
            client = await self._get_client()
            resp = await client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                max_tokens=300,
                temperature=0.3
            )
            if resp.choices and resp.choices[0].message.content:
                return resp.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Denoise failed: {e}")
        
        # Fallback
        return f"User: {user_content}\nAI: {ai_content}"

    def sanitize_content(self, text: str) -> str:
        """
        清洗内容：
        1. 优先提取 <chat> 标签内的内容（如果存在）。
        2. 如果没有 <chat> 标签（如用户消息），则退回常规清洗。
        """
        if not text:
            return ""
            
        # 0. 预处理
        
        # 特殊处理 Image Summary，保留语义
        # [Image Summary: cute cat] -> 图片内容: cute cat
        text = re.sub(r'\[Image Summary\s*:(.*?)\]', r'图片内容:\1', text, flags=re.IGNORECASE)

        # 去除系统占位符 (防止噪音进入向量库)
        placeholders = [
            "[Voice: Processing...]",
            "[Image: Processing...]"
        ]
        for ph in placeholders:
            text = text.replace(ph, "") 

        # 1. 尝试提取 <chat> 标签内容
        # 注意: 历史记录中的 chat 标签可能包含属性 (如 reply="123"), 需兼容 <chat...>
        # 对应 SenderService 生成格式: <chat reply="...">...</chat>
        chat_matches = re.findall(r'<chat[^>]*>(.*?)</chat>', text, flags=re.DOTALL | re.IGNORECASE)
        
        if chat_matches:
            # 如果存在 <chat> 标签，只保留标签内的内容
            # 拼接多段 chat 内容
            full_content = " ".join([m.strip() for m in chat_matches])
            return re.sub(r'\s+', ' ', full_content).strip()
            
        # 2. Fallback: 如果没有 <chat> 标签 (常见于 User 消息或旧数据)
        # 仍然去除可能存在的其他 XML 标签以防噪音，但保留文本
        text = re.sub(r'<[^>]+>', '', text)
        text = re.sub(r'\s+', ' ', text).strip()
        
        return text

    async def _embed_texts(self, texts: List[str]) -> List[List[float]]:
        """调用 OpenAI API 获取 Embeddings"""
        if not texts:
            return []
            
        try:
            client = await self._get_client()
            configs = await config_service.get_all_settings()
            model_name = configs.get("vector_model_name", "text-embedding-3-small")

            resp = await client.embeddings.create(
                input=texts,
                model=model_name
            )
            
            # 强行截断到 1536 维
            # 无论模型原生维度是多少，统一截断以适配 sqlite-vec 表结构。
            # 对于支持 Matryoshka 的模型（如 OpenAI v3, Gemini），前 1536 维即为有效表征。
            return [data.embedding[:1536] for data in resp.data]
        except Exception as e:
            logger.error(f"Embedding API failed: {e}")
            raise

    async def run_background_sync(self):
        """
        [ETL Core] 后台同步循环 (The "Cron")
        策略: Context Barrier + Turn-based Assembly + Denoising
        只处理已经跌出活跃窗口 (Tier 1 -> Tier 2) 的消息。
        """
        self._etl_debug("RAG ETL: Starting background sync cycle...")
        
        # 1. 获取所有活跃的 Chat ID
        # 简单起见，从 Recent History 找，或者遍历所有 Chat 配置。
        # 这里先只扫描最近活跃的 Top 20 Chat
        async for session in get_db_session():
            try:
                # Find chats with recent activity (Exclude Private Chats: chat_id < 0)
                recent_chats_res = await session.execute(
                    text("SELECT DISTINCT chat_id FROM history WHERE chat_id < 0 ORDER BY id DESC LIMIT 20")
                )
                chat_ids = [r.chat_id for r in recent_chats_res.fetchall()]
                
                for chat_id in chat_ids:
                    await self._process_chat_etl(session, chat_id)
                    
            except Exception as e:
                logger.error(f"RAG ETL Global Loop failed: {e}")

    async def _process_chat_etl(self, session, chat_id: int):
        """处理单个 Chat 的 ETL"""
        from core.history_service import history_service
        
        try:
            # 1. 计算 Context Barrier (活跃窗口边界)
            configs = await config_service.get_all_settings()
            max_tokens = int(configs.get("history_tokens", 4000)) # Default 4k
            
            # Reuse the EXACT same logic as /stats to determine the "Active Window" boundary
            # This prevents any discrepancy between what User sees and what RAG sees.
            stats = await history_service.get_session_stats(chat_id, max_tokens)
            active_window_start_id = stats["win_start_id"]
            
            self._etl_debug(f"RAG ETL: Chat {chat_id} | BarrierID (from HistoryService): {active_window_start_id}")
            
            if active_window_start_id == 0:
                 return




            # 2. 扫描 T2 区 (ID < active_window_start_id) 中的未处理项
            # 条件: 是 Assistant 消息 (Turn End), 且 rag_status 为空
            # 且不包含 'Processing...'
            stmt_candidates = text("""
                SELECT h.id 
                FROM history h
                LEFT JOIN rag_status s ON h.id = s.msg_id
                WHERE h.chat_id = :cid
                  AND h.id < :barrier
                  AND h.role = 'assistant'
                  AND s.msg_id IS NULL
                  AND h.content NOT LIKE '[%: Processing...]'
                ORDER BY h.id ASC
                LIMIT 50
            """)
            
            cand_res = await session.execute(stmt_candidates, {"cid": chat_id, "barrier": active_window_start_id})
            candidate_ids = [r.id for r in cand_res.fetchall()]
            
            if not candidate_ids:
                # --- 诊断与自动清理逻辑 ---
                # 1. 查找 Barrier 以下的所有未处理消息
                stmt_pending_all = text("""
                    SELECT h.id, h.role, SUBSTR(h.content, 1, 50) as snippet 
                    FROM history h
                    LEFT JOIN rag_status s ON h.id = s.msg_id
                    WHERE h.chat_id = :cid AND h.id < :barrier AND s.msg_id IS NULL
                    LIMIT 50
                """)
                res = await session.execute(stmt_pending_all, {"cid": chat_id, "barrier": active_window_start_id})
                all_orphans = res.fetchall()
                
                if all_orphans:
                    processed_ids = []
                    # 2. 自动清理: System 消息直接标记 SKIPPED, 距离 Barrier 太远(>30)的用户消息标记 SKIPPED
                    for o in all_orphans:
                        if o.role == 'system' or (active_window_start_id - o.id > 30):
                            await session.execute(
                                text("INSERT OR IGNORE INTO rag_status (msg_id, chat_id, status, processed_at) VALUES (:id, :cid, 'SKIPPED', CURRENT_TIMESTAMP)"),
                                {"id": o.id, "cid": chat_id}
                            )
                            processed_ids.append(o.id)
                    
                    if processed_ids:
                        await session.commit()
                        self._etl_debug(f"RAG ETL: Auto-cleaned {len(processed_ids)} orphans (System/Old) for Chat {chat_id}.")
                        await self._notify_admin(f"🧹 <b>ETL 自动清理 [Chat {chat_id}]</b>\n已清理 {len(processed_ids)} 条系统/过时消息（这些消息通常不含 RAG 价值）。")
                return

            self._etl_debug(f"RAG ETL: Chat {chat_id} has {len(candidate_ids)} candidates falling out of context (barrier: {active_window_start_id}).")

            # 3. Process each Candidate (Turn Assembly)
            for anchor_id in candidate_ids:
                await self._process_single_turn(session, chat_id, anchor_id)
                
        except Exception as e:
            logger.error(f"RAG ETL failed for chat {chat_id}: {e}")

    async def _process_single_turn(self, session, chat_id: int, anchor_id: int):
        """
        处理单个交互轮次
        anchor_id 是 AI 的一条消息 ID。需向前/向后拼装完整轮次。
        """
        # 3.1 Gather AI Block (Backwards & Forwards)
        # 我们的 Anchor 是 Candidate 扫出来的，可能是 AI Block 的中间某一条。
        # 但我们之前逻辑是：Candidate 是 "Unprocessed Assistant Msg".
        # 只要我们处理完标记了，就不会重复扫。
        
        # 这里的策略：以 Anchor 为核心，向后找 AI (直到 User), 向前找 AI (直到 User) 组成 AI Block.
        # 然后再向前找 User 组成 User Block.
        
        # 简化策略: 
        # 1. Anchor 必定是 AI。
        # 2. 向前找连续 AI -> 合并
        # 3. 再向前找连续 User -> 合并为 User Block
        
        # Look back for AI chain start
        # 其实更简单的做法是：每次只处理 AI Block 的**最后一条**作为 Head？
        # 不行，因为我们扫出的是 "所有未处理的 AI"。
        # 如果一个 AI Block 有 3 条，我们会扫出 3 个 Candidate。
        # 我们处理第一个时，如果不把后面两个标记掉，下次循环还会扫到。
        
        # 所以：一旦处理，必须把整个 Block 的 ID 都标记好。
        
        # Fetch surrounding messages (Window 20 is enough for a turn)
        stmt_surround = text("""
            SELECT id, role, content FROM history 
            WHERE chat_id=:cid AND id BETWEEN :low AND :high
            ORDER BY id ASC
        """)
        rows = (await session.execute(stmt_surround, {"cid": chat_id, "low": anchor_id - 10, "high": anchor_id + 5})).fetchall()
        
        # Find Anchor index
        anchor_idx = -1
        for i, r in enumerate(rows):
            if r.id == anchor_id:
                anchor_idx = i
                break
        
        if anchor_idx == -1: return # Should not happen

        # Expand AI Block
        ai_ids = [anchor_id]
        ai_content = [rows[anchor_idx].content]
        
        # Look forward (Next is AI?)
        curr = anchor_idx + 1
        while curr < len(rows) and rows[curr].role == 'assistant':
            ai_ids.append(rows[curr].id)
            ai_content.append(rows[curr].content)
            curr += 1
            
        # Look backward (Prev is AI?)
        curr = anchor_idx - 1
        while curr >= 0 and rows[curr].role == 'assistant':
            ai_ids.insert(0, rows[curr].id)
            ai_content.insert(0, rows[curr].content)
            curr -= 1
            
        # Update Anchor to be the LAST ID of the AI Block (Standard Convention)
        real_head_id = ai_ids[-1]
        
        # 如果 real_head_id 已经被处理过(在 rag_status 里)，那整个 Block 都跳过
        # (check DB)
        chk = await session.execute(text("SELECT 1 FROM rag_status WHERE msg_id=:mid"), {"mid": real_head_id})
        if chk.scalar():
            # Mark curent anchor as SKIPPED just in case
             if anchor_id != real_head_id:
                 await session.execute(text("INSERT OR IGNORE INTO rag_status (msg_id, chat_id, status, processed_at) VALUES (:id, :cid, 'SKIPPED', CURRENT_TIMESTAMP)"), 
                                       {"id": anchor_id, "cid": chat_id})
                 await session.commit()
             return

        # Look backward for User Block (User Question)
        # Start searching from before the first AI msg
        user_ids = []
        user_content = []
        
        search_idx = -1
        # Find index of first AI msg in 'rows'
        first_ai_id = ai_ids[0]
        for i, r in enumerate(rows):
            if r.id == first_ai_id:
                search_idx = i - 1
                break
        
        while search_idx >= 0:
            if rows[search_idx].role == 'user':
                user_ids.insert(0, rows[search_idx].id) # Prepend
                user_content.insert(0, rows[search_idx].content)
                search_idx -= 1
            elif rows[search_idx].role == 'system':
                # 遇到系统消息 (例如 Reaction)，标记为 TAIL/SKIPPED 并继续向前回溯
                # 这解决了系统消息打断用户消息链的问题
                await session.execute(
                    text("INSERT OR IGNORE INTO rag_status (msg_id, chat_id, status, processed_at) VALUES (:id, :cid, 'SKIPPED', CURRENT_TIMESTAMP)"),
                    {"id": rows[search_idx].id, "cid": chat_id}
                )
                search_idx -= 1
            else:
                # 遇到其他角色 (通常是上一轮的 Assistant)，停止回溯
                break
            
        # Assembly
        full_user_text = "\n".join(user_content)
        full_ai_text = "\n".join(ai_content)
        
        if not full_user_text:
            # Orphan AI response? Maybe system msg before?
            full_user_text = "(Context missing or System trigger)"
            
        # 4. Denoise
        denoised_text = await self.denoise_interaction(full_user_text, full_ai_text)
        
        # 5. Embed
        vecs = await self._embed_texts([denoised_text])
        if not vecs: return
        vector = vecs[0]
        
        # 6. Store
        # Head (Last AI ID) -> HEAD + Vector + Denoised
        # Others -> TAIL/SKIPPED
        
        # Head
        await session.execute(
            text("""
                INSERT OR REPLACE INTO rag_status (msg_id, chat_id, status, denoised_content, processed_at) 
                VALUES (:id, :cid, 'HEAD', :content, CURRENT_TIMESTAMP)
            """), 
            {"id": real_head_id, "cid": chat_id, "content": denoised_text}
        )
        
        await session.execute(
            text("INSERT INTO history_vec(rowid, embedding) VALUES (:id, :vec)"),
            {"id": real_head_id, "vec": json.dumps(vector)}
        )
        
        # Tails (Other AI parts)
        for aid in ai_ids:
            if aid != real_head_id:
                await session.execute(
                    text("INSERT OR IGNORE INTO rag_status (msg_id, chat_id, status, processed_at) VALUES (:id, :cid, 'TAIL', CURRENT_TIMESTAMP)"),
                    {"id": aid, "cid": chat_id}
                )
                
        # Users (Linked parts)
        for uid in user_ids:
            await session.execute(
                text("INSERT OR IGNORE INTO rag_status (msg_id, chat_id, status, processed_at) VALUES (:id, :cid, 'TAIL', CURRENT_TIMESTAMP)"),
                {"id": uid, "cid": chat_id}
            )

        await session.commit()
        self._etl_debug(f"RAG ETL: Indexed Turn {real_head_id} (User: {len(user_ids)}, AI: {len(ai_ids)})")
        
        # 7. 通知管理员
        msg = (
            f"✅ <b>RAG ETL 完成</b>\n"
            f"📍 Chat: <code>{chat_id}</code>\n"
            f"🔗 Turn Head: {real_head_id}\n\n"
            f"<b>🧠 事实化内容 (Denoised):</b>\n"
            f"<code>{html.escape(denoised_text)}</code>"
        )
        await self._notify_admin(msg)

    async def contextualize_query(self, query_text: str, conversation_history: str, long_term_summary: str = "") -> str:
        """
        [Query Rewriting]
        使用摘要模型快速重写查询，消除指代不明。
        现在接收与主模型完全一致的 Full Context (Active Window + Summary)。
        """
        # [DEBUG] Log entry
        self._etl_debug(f"RAG Rewriter: Input='{query_text}' (Len: {len(query_text)})")



        try:
            configs = await config_service.get_all_settings()
            summary_model = configs.get("summary_model_name")
            
            # 如果没配摘要模型，则降级使用主模型；如果主模型也没配，则跳过
            if not summary_model:
                summary_model = configs.get("model_name")
            
            if not summary_model:
                logger.warning("RAG Rewriter: Skipped (No Model Configured)")
                return query_text

            self._etl_debug(f"RAG Rewriter: Using model '{summary_model}'")

            client = await self._get_client()
            
            # 构建轻量级 Context
            # context_msgs 应该是 ["User: ...", "Assistant: ..."] 的最近几条
            sys_prompt = (
                "你是一名查询优化专家。"
                "你的目标是将用户的最新输入重写为适合数据库检索的简洁查询语句。"
                "1. 指代消歧：将'它'、'那个'等代词替换为上下文中讨论的具体对象。"
                "2. 补充背景：如果用户的话依赖前文（如追问原因），请把主语和背景补全。"
                "3. 多模态融合：如果输入包含 [Image Summary: ...]，且对查询有帮助，请提取核心语义。对于语音转录的文本，直接视为用户对白的有效组成部分。"
                "4. 去噪精简：坚决去除所有情绪词（如'吓死'、'哈哈'）、口语废话（如'我想想'、'不知道'）和抱怨。只保留事实性关键词。"
                "5. 输出格式：输出一句清晰、客观的陈述句或问句，不要包含任何解释。"
                "只输出重写后的字符串。"
            )
            
            # Construct Rich Context Block
            full_context_block = ""
            if long_term_summary:
                full_context_block += f"=== Long-term Memory ===\n{long_term_summary}\n\n"
            
            full_context_block += f"=== Active Conversation ===\n{conversation_history}"

            resp = await client.chat.completions.create(
                model=summary_model,
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": f"{full_context_block}\n\nUser Input to Rewrite:\n{query_text}"}
                ],
                max_tokens=200,
                temperature=0.3
            )
            
            if resp.choices and resp.choices[0].message.content:
                new_query = resp.choices[0].message.content.strip()
                # 移除可能误带的引号
                if new_query.startswith('"') and new_query.endswith('"'):
                    new_query = new_query[1:-1]
                
                if new_query != query_text:
                    self._etl_debug(f"RAG Rewriter: '{query_text}' -> '{new_query}'")
                    try:
                        import core.bot as bot_module
                        if settings.RAG_NOTIFY_ADMIN and bot_module.bot:
                            rewrite_msg = (
                                f"🔄 <b>RAG Query Rewritten</b>\n"
                                f"From: <code>{html.escape(query_text)}</code>\n"
                                f"To: <code>{html.escape(new_query)}</code>"
                            )
                            await bot_module.bot.send_message(settings.ADMIN_USER_ID, rewrite_msg, parse_mode='HTML')
                    except Exception as notify_e:
                        logger.error(f"Failed to send rewrite debug: {notify_e}")
                else:
                    self._etl_debug(f"RAG Rewriter: No change ('{new_query}').")
                
                return new_query
            
        except Exception as e:
            logger.warning(f"Query Rewrite failed: {e}")
            
        return query_text

    async def search_context(self, chat_id: int, query_text: str, exclude_ids: Optional[List[int]] = None, top_k: int = 5, context_padding: int = 3) -> str:
        """
        检索相关上下文 (Context Window Expansion)
        
        策略:
        1. Vector Search: 找到 Top K 核心匹配 (Anchors).
        2. Expansion: 对每个 Anchor，基于逻辑顺序查询前后文 (解决 ID Gap 问题).
        3. Clustering: 合并重叠的上下文窗口.
        4. Formatting: 输出带焦点的对话块.

        :param exclude_ids: 需要排除的消息 ID 列表 (避免自引用)
        :param context_padding: 每个 Anchor 前后扩展的消息数量
        """
        sanitized_query = self.sanitize_content(query_text)
        if len(sanitized_query) < 2:
            return ""

        limit = top_k if top_k else self.DEFAULT_TOP_K
        
        configs = await config_service.get_all_settings()
        
        # 动态读取 Padding 配置
        current_padding = context_padding
        try:
            if val := configs.get("rag_context_padding"):
                current_padding = int(val)
        except: pass
        
        threshold = self.DEFAULT_SIMILARITY_THRESHOLD
        try:
            if val := configs.get("rag_similarity_threshold"):
                threshold = float(val)
        except: pass

        # [DEBUG] Start Notification
        try:
            import core.bot as bot_module
            if bot_module.bot:
                start_msg = (
                    f"🔍 <b>RAG Search: Interaction Mode</b>\n"
                    f"Chat: <code>{chat_id}</code> | Q: <code>{html.escape(sanitized_query)}</code>\n"
                    f"TopK: {limit} | Pad: {current_padding}"
                )
                await bot_module.bot.send_message(settings.ADMIN_USER_ID, start_msg, parse_mode='HTML')
        except: pass

        try:
            # 1. Get Query Vector
            query_vecs = await self._embed_texts([sanitized_query])
            if not query_vecs:
                print(f"[DEBUG] No query vector generated", file=sys.stderr)
                return ""
            query_vec = query_vecs[0]
            
            async for session in get_db_session():
                # ---------------------------------------------------------
                # Step 1: Vector Search (Find Anchors)
                # ---------------------------------------------------------
                exclusion_clause = ""
                params = {
                    "chat_id": chat_id, 
                    "query_vec": json.dumps(query_vec),
                    "threshold": threshold,
                    "top_k": limit
                }
                
                if exclude_ids:
                    exclusion_clause = "AND h.id NOT IN :exclude_ids"
                    params["exclude_ids"] = tuple(exclude_ids)

                anchor_sql = f"""
                    SELECT h.id, vec_distance_cosine(v.embedding, :query_vec) as distance
                    FROM history_vec v
                    JOIN history h ON v.rowid = h.id
                    WHERE h.chat_id = :chat_id 
                      AND vec_distance_cosine(v.embedding, :query_vec) < :threshold
                      {exclusion_clause}
                    ORDER BY distance ASC
                    LIMIT :top_k
                """
                
                stmt = text(anchor_sql)
                if exclude_ids:
                    stmt = stmt.bindparams(bindparam("exclude_ids", expanding=True))
                
                result = await session.execute(stmt, params)
                anchors = result.fetchall()  # [(id, distance), ...]
                
                if not anchors:
                    return ""
                
                anchor_map = {row.id: row.distance for row in anchors}
                sorted_anchor_ids = [row.id for row in anchors]

                # ---------------------------------------------------------
                # Step 2: Logical Expansion (Fixing ID Gaps)
                # ---------------------------------------------------------
                # Clusters: List[Set[int]] - 初始每个 Anchor 一个 Cluster
                clusters: List[set] = []

                for anchor_id in sorted_anchor_ids:
                    # 获取前文 (Pre-context)
                    # 倒序取 limit，结果需反转
                    pre_sql = text("""
                        SELECT id FROM history 
                        WHERE chat_id = :cid AND id < :aid 
                        ORDER BY id DESC LIMIT :pad
                    """)
                    pre_res = await session.execute(pre_sql, {"cid": chat_id, "aid": anchor_id, "pad": current_padding})
                    pre_ids = [r.id for r in pre_res.fetchall()]
                    
                    # 获取后文 (Post-context)
                    post_sql = text("""
                        SELECT id FROM history 
                        WHERE chat_id = :cid AND id > :aid 
                        ORDER BY id ASC LIMIT :pad
                    """)
                    post_res = await session.execute(post_sql, {"cid": chat_id, "aid": anchor_id, "pad": current_padding})
                    post_ids = [r.id for r in post_res.fetchall()]




                    # 组装当前 Cluster
                    current_cluster = set(pre_ids + [anchor_id] + post_ids)
                    clusters.append(current_cluster)

                # ---------------------------------------------------------
                # Step 3: Cluster Merging
                # ---------------------------------------------------------
                # 贪婪合并：如果有交集，则合并
                merged_clusters: List[set] = []
                
                while clusters:
                    base = clusters.pop(0)
                    # 尝试与后续所有 cluster 合并
                    i = 0
                    while i < len(clusters):
                        candidate = clusters[i]
                        if not base.isdisjoint(candidate):
                            base.update(candidate)
                            clusters.pop(i) # 移除已被合并的
                        else:
                            i += 1
                    merged_clusters.append(base)

                # ---------------------------------------------------------
                # Step 4: Content Fetching
                # ---------------------------------------------------------
                # 收集所有需要查询的 Unique ID
                all_needed_ids = set()
                for c in merged_clusters:
                    all_needed_ids.update(c)
                
                if not all_needed_ids:
                    return ""

                # 批量获取内容
                fetch_sql = text("SELECT id, role, content, timestamp FROM history WHERE id IN :ids")
                fetch_stmt = fetch_sql.bindparams(bindparam("ids", expanding=True))
                fetch_res = await session.execute(fetch_stmt, {"ids": tuple(all_needed_ids)})
                
                # ID -> Message Object
                msg_map = {
                    row.id: {
                        "role": row.role,
                        "content": row.content,
                        "timestamp": row.timestamp
                    } 
                    for row in fetch_res.fetchall()
                }

                # ---------------------------------------------------------
                # Step 5: Formatting with Focus Highlighting
                # ---------------------------------------------------------
                output_blocks = []
                
                # 对 Merged Clusters 按其中最小 ID 排序，保证时间序
                merged_clusters.sort(key=lambda s: min(s))

                for cluster in merged_clusters:
                    # Cluster 内部按 ID 排序
                    sorted_ids = sorted(list(cluster))
                    block_lines = []
                    
                    for mid in sorted_ids:
                        msg = msg_map.get(mid)
                        if not msg: continue
                        
                        # Date Formatting
                        date_str = "Unknown"
                        if msg["timestamp"]:
                            if hasattr(msg["timestamp"], 'strftime'):
                                date_str = msg["timestamp"].strftime("%Y-%m-%d %H:%M")
                            else:
                                date_str = str(msg["timestamp"])[:16]

                        content = self.sanitize_content(msg["content"])
                        line = f"[{date_str}] {msg['role'].capitalize()}: {content}"

                        # Check if this is an Anchor
                        if mid in anchor_map:
                            dist = anchor_map[mid]
                            # Highlight Anchor
                            line = f">>> {line} (Match: {dist:.3f}) <<<"
                        
                        block_lines.append(line)
                    
                    output_blocks.append("\n".join(block_lines))

                # Join blocks with explicit separator
                final_context = "\n\n... (Context Skip) ...\n\n".join(output_blocks)

                # [DEBUG] Success Notification
                try:
                    import core.bot as bot_module
                    if bot_module.bot:
                        debug_msg = (
                            f"✅ <b>RAG Result: Interaction Mode</b>\n"
                            f"Blocks: {len(output_blocks)} | Total Msgs: {len(all_needed_ids)}\n"
                            f"<pre>{html.escape(final_context[:3000])}</pre>" # Truncate for TG
                        )
                        await bot_module.bot.send_message(settings.ADMIN_USER_ID, debug_msg, parse_mode='HTML')
                except: pass

                return final_context

        except Exception as e:
            logger.error(f"RAG Search failed: {e}", exc_info=True)
            return ""

    async def clear_chat_vectors(self, chat_id: int):
        """
        清除指定会话的所有向量数据和RAG状态 (物理删除)
        用于 /reset 或 Rebuild Index
        """
        async for session in get_db_session():
            try:
                # 1. Clear rag_status (The Knowledge Base)
                await session.execute(
                    text("DELETE FROM rag_status WHERE chat_id = :chat_id"),
                    {"chat_id": chat_id}
                )

                # 2. Clear history_vec (The Vector Index)
                # 通过子查询删除 history_vec 中对应的 rowid
                # 假设 history_vec 是虚拟表或普通表，rowid 对应 history.id
                await session.execute(
                    text("""
                        DELETE FROM history_vec 
                        WHERE rowid IN (
                            SELECT id FROM history WHERE chat_id = :chat_id
                        )
                    """),
                    {"chat_id": chat_id}
                )
                await session.commit()
                
                # 清除冷却状态，允许立即重新同步
                if chat_id in self._sync_cooldowns:
                    del self._sync_cooldowns[chat_id]
                    
                logger.info(f"RAG: Cleared all vectors & status for chat {chat_id}")
            except Exception as e:
                logger.error(f"RAG Clear failed for chat {chat_id}: {e}")

    async def clear_all_vectors(self):
        """
        清除所有会话的向量数据 (全局重置)
        """
        async for session in get_db_session():
            try:
                # 1. Clear rag_status
                await session.execute(text("DELETE FROM rag_status"))

                # 2. Clear history_vec
                await session.execute(text("DELETE FROM history_vec"))
                
                await session.commit()
                self._sync_cooldowns.clear()
                logger.info("RAG: Global Index Cleared.")
            except Exception as e:
                logger.error(f"Global RAG Clear failed: {e}")

    async def rebuild_index(self, chat_id: int = None):
        """
        Rebuild Index
        如果指定 chat_id，只清除该会话。
        如果不指定 (None)，则清除所有 (Global).
        """
        if chat_id:
            await self.clear_chat_vectors(chat_id)
        else:
            await self.clear_all_vectors()

    async def get_vector_stats(self, chat_id: int) -> Dict[str, Any]:
        """
        获取指定会话的 RAG 统计 (v2)
        分类:
        1. Knowledge Base (Indexed): 已降噪并入库的 Facts.
        2. Pending (ETL Queue): 已跌出上下文窗口，等待降噪的.
        3. Active (Hot): 仍在上下文窗口内，无需索引的.
        """
        async for session in get_db_session():
            try:
                # 1. 计算 Context Barrier (Call HistoryService directly)
                from core.history_service import history_service
                
                configs = await config_service.get_all_settings()
                max_tokens = int(configs.get("history_tokens", 4000))

                # Reuse exact logic
                stats = await history_service.get_session_stats(chat_id, max_tokens)
                barrier_id = stats["win_start_id"]
                
                # Simple count for active window
                stmt_active_count = text("SELECT COUNT(*) FROM history WHERE chat_id=:cid AND id >= :barrier")
                active_count = 0
                if barrier_id > 0:
                    active_count = (await session.execute(stmt_active_count, {"cid": chat_id, "barrier": barrier_id})).scalar() or 0



                # 2. Count Indexed (HEAD)
                stmt_indexed = text("SELECT COUNT(*) FROM rag_status WHERE chat_id = :cid AND status = 'HEAD'")
                indexed_count = (await session.execute(stmt_indexed, {"cid": chat_id})).scalar() or 0

                # 3. Count Pending (Assistant Msgs < Barrier NOT IN rag_status)
                pending_count = 0
                if barrier_id > 0:
                    stmt_pending = text("""
                        SELECT COUNT(*) FROM history h
                        LEFT JOIN rag_status s ON h.id = s.msg_id
                        WHERE h.chat_id = :cid
                          AND h.id < :barrier
                          AND s.msg_id IS NULL
                          AND h.content NOT LIKE '[%: Processing...]'
                    """)
                    pending_count = (await session.execute(stmt_pending, {"cid": chat_id, "barrier": barrier_id})).scalar() or 0

                # 4. Count Active (Approximate)
                # Just return a status string or boolean?
                # Let's count Assistant msgs in active window for completeness

                # Re-query simplified for active assistant count if needed, or just omit.
                # User cares about: "How many indexed?" vs "How many waiting?"

                # Cooldown
                cooldown_left = 0
                if chat_id in self._sync_cooldowns:
                     passed = time.time() - self._sync_cooldowns[chat_id]
                     if passed < self.SYNC_COOLDOWN_SECONDS:
                         cooldown_left = int(self.SYNC_COOLDOWN_SECONDS - passed)

                return {
                    "indexed": indexed_count,
                    "pending": pending_count,
                    "active_window_size": active_count, # Msgs in active window
                    "cooldown_left": cooldown_left
                }
            except Exception as e:
                logger.error(f"RAG Stats failed: {e}")
                return {"error": str(e)}

rag_service = RagService()
