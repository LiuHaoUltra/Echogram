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
    SYNC_COOLDOWN_SECONDS = 180  # 3分钟熔断冷却

    def __init__(self):
        self._client = None
        self._current_api_key = None
        self._current_base_url = None
        self._sync_cooldowns: Dict[int, float] = {}  # chat_id -> last_failure_time
    
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

    async def _check_and_migrate_status(self, session, chat_id: int):
        """
        [One-off Migration] 迁移旧的状态追踪方式 (Zero Vectors) 到新表 (rag_status)
        """
        try:
            # Check if already migrated (any record exists)
            count_res = await session.execute(
                text("SELECT 1 FROM rag_status WHERE chat_id=:cid LIMIT 1"), 
                {"cid": chat_id}
            )
            if count_res.scalar():
                return 

            # Check if legacy data exists
            legacy_count = await session.execute(
                text("SELECT COUNT(*) FROM history_vec v JOIN history h ON v.rowid=h.id WHERE h.chat_id=:cid"), 
                {"cid": chat_id}
            )
            if legacy_count.scalar() == 0:
                return

            logger.warning(f"RAG: Migrating chat {chat_id} to 'rag_status' table...")

            # 1. Identify Tails (Zero Vectors) -> Insert 'TAIL'
            # Use shotgun strategy to catch any zero vector format
            await session.execute(text("""
                INSERT INTO rag_status (msg_id, chat_id, status)
                SELECT v.rowid, :cid, 'TAIL'
                FROM history_vec v
                JOIN history h ON v.rowid = h.id
                WHERE h.chat_id = :cid
                  AND (v.embedding LIKE '[0.0, 0.0%' OR v.embedding LIKE '[0.0,0.0%' OR v.embedding LIKE '[0, 0%')
            """), {"cid": chat_id})

            # 2. Identify Heads (Real Vectors) -> Insert 'HEAD'
            # Any vector in DB that is NOT in rag_status yet must be a Head
            await session.execute(text("""
                INSERT INTO rag_status (msg_id, chat_id, status)
                SELECT v.rowid, :cid, 'HEAD'
                FROM history_vec v
                JOIN history h ON v.rowid = h.id
                WHERE h.chat_id = :cid
                  AND v.rowid NOT IN (SELECT msg_id FROM rag_status WHERE chat_id=:cid)
            """), {"cid": chat_id})

            # 3. Clean up Tails from history_vec (Free up space/indices)
            await session.execute(text("""
                DELETE FROM history_vec
                WHERE rowid IN (
                    SELECT msg_id FROM rag_status WHERE chat_id=:cid AND status='TAIL'
                )
            """), {"cid": chat_id})

            await session.commit()
            logger.info(f"RAG: Migration completed for chat {chat_id}")

        except Exception as e:
            logger.error(f"RAG Migration failed: {e}")
            # Do not re-raise, allow sync to proceed (fallback)

    async def sync_historic_embeddings(self, chat_id: int):
        """
        懒惰全量同步 (Lazy Full-Sync) - Interaction-Centric Mode
        只索引 AI 消息，并自动融合前序用户问题 (User Context + AI Response)。
        """
        configs = await config_service.get_all_settings()
        
        # 动态读取冷却时间
        cooldown = self.SYNC_COOLDOWN_SECONDS
        try:
            if val := configs.get("rag_sync_cooldown"):
                cooldown = int(val)
        except: pass

        # 1. 熔断检查
        last_fail = self._sync_cooldowns.get(chat_id, 0)
        if time.time() - last_fail < cooldown:
            return

        async for session in get_db_session():
            try:
            try:
                # 0. Migration Check (Sync-time migration)
                await self._check_and_migrate_status(session, chat_id)

                # 2. 找出所有未嵌入的 AI 消息 (Anchors)
                # 使用 rag_status 判断是否已处理
                stmt = text("""
                    SELECT h.id, h.role, h.content 
                    FROM history h
                    LEFT JOIN rag_status s ON h.id = s.msg_id
                    WHERE h.chat_id = :chat_id 
                      AND h.role = 'assistant'
                      AND s.msg_id IS NULL
                      AND h.content IS NOT NULL
                      AND h.content != ''
                      AND h.content NOT LIKE '[%: Processing...]'
                    LIMIT 50
                """)
                
                result = await session.execute(stmt, {"chat_id": chat_id})
                ai_rows = result.fetchall()
                
                if not ai_rows:
                    if chat_id in self._sync_cooldowns:
                        del self._sync_cooldowns[chat_id]
                    return

                logger.info(f"RAG Sync: Found {len(ai_rows)} AI anchors for chat {chat_id}")

                # 3. Context Fusion Loop (Sequential Merging)
                db_write_ops = [] # list of (id, vector_or_placeholder)
                texts_to_embed = [] # list of strings
                text_map_indices = [] # indices in db_write_ops that need embedding filling
                
                processed_ids = set() # ids handled in this batch (as Head or Tail)

                for ai_row in ai_rows:
                    if ai_row.id in processed_ids:
                        continue
                    
                    # 3.1 Check Left Context (Is this a Tail?)
                    # Lookback 1 message
                    prev_sql = text("SELECT role FROM history WHERE chat_id = :cid AND id < :aid ORDER BY id DESC LIMIT 1")
                    prev_res = await session.execute(prev_sql, {"cid": chat_id, "aid": ai_row.id})
                    prev_row = prev_res.fetchone()
                    
                    is_tail = False
                    if prev_row and prev_row.role == 'assistant':
                        is_tail = True
                    
                    if is_tail:
                        # [Tail Strategy]
                        # 这是一个 "掉队" 的后续气泡 (上一条也是 AI)。
                        # 它的内容理应被合并在 Head 里。
                        # 如果 Head 已经索引过，我们无法追溯更新 Head (代价太大)。
                        # 所以策略是：直接静默标记为已处理 (Zero Vector)，不生成独立索引 (避免污染)。
                        processed_ids.add(ai_row.id)
                        db_write_ops.append((ai_row.id, "ZERO"))
                        continue

                    # [Head Strategy]
                    # 这是由 User 触发的第一条 AI 消息 (Head)。
                    # 我们需要向后由贪婪抓取所有连续的 AI 消息 (Tails)，合并内容。
                    
                    # 3.2 Look Ahead (Find Consequent Tails)
                    # 限制抓取 10 条，避免无限循环
                    next_sql = text("""
                        SELECT id, content, role FROM history 
                        WHERE chat_id = :cid AND id > :aid 
                        ORDER BY id ASC LIMIT 10
                    """)
                    next_res = await session.execute(next_sql, {"cid": chat_id, "aid": ai_row.id})
                    next_rows = next_res.fetchall()
                    
                    chain_content = [self.sanitize_content(ai_row.content)]
                    chain_ids = [ai_row.id]
                    
                    for nr in next_rows:
                        if nr.role == 'assistant':
                            # Found a tail
                            chain_content.append(self.sanitize_content(nr.content))
                            chain_ids.append(nr.id)
                        else:
                            # Met User/System -> Stop
                            break
                            
                    # Mark all as processed
                    for cid in chain_ids:
                        processed_ids.add(cid)
                        
                    # 3.3 Look Back (Get User Context)
                    # 抓取最近的 User 消息 (最多 3 条连续)
                    lb_sql = text("""
                        SELECT role, content FROM history 
                        WHERE chat_id = :cid AND id < :aid 
                        ORDER BY id DESC LIMIT 5
                    """)
                    lb_res = await session.execute(lb_sql, {"cid": chat_id, "aid": ai_row.id})
                    lb_rows = lb_res.fetchall()
                    
                    user_context_parts = []
                    for prev_msg in lb_rows:
                        if prev_msg.role == 'user':
                            prev_content = self.sanitize_content(prev_msg.content)
                            if prev_content:
                                user_context_parts.insert(0, prev_content)
                                if len(user_context_parts) >= 3: 
                                    break
                        else:
                            break
                            
                    # 3.4 Build Fused Text
                    merged_ai_content = " ".join([c for c in chain_content if c])
                    
                    fused_text = ""
                    if user_context_parts:
                        user_block = " ".join(user_context_parts)
                        fused_text = f"User: {user_block}\nAssistant: {merged_ai_content}"
                    else:
                        fused_text = f"Assistant: {merged_ai_content}"
                    
                    # Register for embedding
                    texts_to_embed.append(fused_text)
                    
                    # Head gets the vector
                    db_write_ops.append((ai_row.id, "PENDING")) 
                    text_map_indices.append(len(db_write_ops) - 1)
                    
                    # Tails get ZERO
                    for tail_id in chain_ids[1:]:
                        db_write_ops.append((tail_id, "ZERO"))

                if not db_write_ops:
                    return

                # 4. Batch Embed
                embeddings = []
                if texts_to_embed:
                    embeddings = await self._embed_texts(texts_to_embed)
                
                # Fill PENDING with vectors
                real_vectors_map = {idx: vec for idx, vec in zip(text_map_indices, embeddings)}
                
                # 5. [DEBUG] Notification
                try:
                    import core.bot as bot_module
                    if bot_module.bot and texts_to_embed:
                        # 构建完整预览 (Max 3500 chars)
                        full_preview = ""
                        for idx, item in enumerate(texts_to_embed):
                            snippet = item.split('\n')[0][:50] + "..." if len(item) > 100 else item
                            full_preview += f"[{idx+1}] {html.escape(snippet)}\n"
                        
                        if len(full_preview) > 3500:
                            full_preview = full_preview[:3500] + "\n... (Truncated)"

                        debug_msg = (
                            f"🔮 <b>RAG Sync: Interaction Mode</b>\n"
                            f"Chat: <code>{chat_id}</code> | Turns: <code>{len(texts_to_embed)}</code> (Merged)\n"
                            f"<pre>{full_preview}</pre>"
                        )
                        await bot_module.bot.send_message(settings.ADMIN_USER_ID, debug_msg, parse_mode='HTML')
                except: pass

                # 6. Write to DB
                # PENDING -> Head (Vector + Status)
                # ZERO -> Tail (Status Only)
                
                for i, (mid, status) in enumerate(db_write_ops):
                    final_vec = None
                    rag_status_val = "TAIL"
                    
                    if status == "PENDING":
                        # Find mapped vector
                        if i in real_vectors_map:
                            final_vec = real_vectors_map[i]
                            rag_status_val = "HEAD"
                    
                    # 1. Update Status Table (Always)
                    # Note: Using INSERT OR IGNORE just in case concurrency
                    await session.execute(
                        text("INSERT OR IGNORE INTO rag_status (msg_id, chat_id, status) VALUES (:id, :cid, :status)"),
                        {"id": mid, "cid": chat_id, "status": rag_status_val}
                    )
                        
                    # 2. Insert Vector (Only if Head/Real)
                    if final_vec:
                        await session.execute(
                            text("INSERT INTO history_vec(rowid, embedding) VALUES (:id, :embedding)"),
                            {"id": mid, "embedding": json.dumps(final_vec)} 
                        )
                
                await session.commit()
                logger.info(f"RAG Sync: Indexed {len(texts_to_embed)} Heads, Skipped {len(db_write_ops) - len(texts_to_embed)} Tails.")
                
                if chat_id in self._sync_cooldowns:
                    del self._sync_cooldowns[chat_id]
            except Exception as e:
                logger.error(f"RAG Sync failed for chat {chat_id}: {e}")
                self._sync_cooldowns[chat_id] = time.time()

    async def contextualize_query(self, query_text: str, conversation_history: str, long_term_summary: str = "") -> str:
        """
        [Query Rewriting]
        使用摘要模型快速重写查询，消除指代不明。
        现在接收与主模型完全一致的 Full Context (Active Window + Summary)。
        """
        # [DEBUG] Log entry
        logger.info(f"RAG Rewriter: Input='{query_text}' (Len: {len(query_text)})")

        # 简单启发式过滤：如果很长，可能不需要重写 (省钱)
        if len(query_text) > 40:
            logger.info("RAG Rewriter: Skipped (Length > 40)")
            return query_text

        try:
            configs = await config_service.get_all_settings()
            summary_model = configs.get("summary_model_name")
            
            # 如果没配摘要模型，则降级使用主模型；如果主模型也没配，则跳过
            if not summary_model:
                summary_model = configs.get("model_name")
            
            if not summary_model:
                logger.warning("RAG Rewriter: Skipped (No Model Configured)")
                return query_text

            logger.info(f"RAG Rewriter: Using model '{summary_model}'")

            client = await self._get_client()
            
            # 构建轻量级 Context
            # context_msgs 应该是 ["User: ...", "Assistant: ..."] 的最近几条
            sys_prompt = (
                "你是一名查询优化专家。"
                "你的目标是将用户的最新输入重写为适合数据库检索的简洁查询语句。"
                "1. 指代消歧：将'它'、'那个'等代词替换为上下文中讨论的具体对象。"
                "2. 补充背景：如果用户的话依赖前文（如追问原因），请把主语和背景补全。"
                "3. 去噪精简：坚决去除所有情绪词（如'吓死'、'哈哈'）、口语废话（如'我想想'、'不知道'）和抱怨。只保留事实性关键词。"
                "4. 输出格式：输出一句清晰、客观的陈述句或问句，不要包含任何解释。"
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
                    logger.info(f"RAG Rewriter: '{query_text}' -> '{new_query}'")
                    try:
                        import core.bot as bot_module
                        if bot_module.bot:
                            rewrite_msg = (
                                f"🔄 <b>RAG Query Rewritten</b>\n"
                                f"From: <code>{html.escape(query_text)}</code>\n"
                                f"To: <code>{html.escape(new_query)}</code>"
                            )
                            await bot_module.bot.send_message(settings.ADMIN_USER_ID, rewrite_msg, parse_mode='HTML')
                    except Exception as notify_e:
                        logger.error(f"Failed to send rewrite debug: {notify_e}")
                else:
                    logger.info(f"RAG Rewriter: No change ('{new_query}').")
                
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
        清除指定会话的所有向量数据 (物理删除)
        用于 /reset 或 Rebuild Index
        """
        async for session in get_db_session():
            try:
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
                    
                logger.info(f"RAG: Cleared all vectors for chat {chat_id}")
            except Exception as e:
                logger.error(f"RAG Clear failed for chat {chat_id}: {e}")

    async def clear_all_vectors(self):
        """
        [Danger] 清除整个数据库的所有向量索引
        用于切换 Embedding 模型时的全局重建
        """
        async for session in get_db_session():
            try:
                await session.execute(text("DELETE FROM history_vec"))
                await session.commit()
                
                # 清除所有冷却
                self._sync_cooldowns.clear()
                
                logger.warning("RAG: GLOBALLY CLEARED all vector indices!")
            except Exception as e:
                logger.error(f"RAG Global Clear failed: {e}")

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
        获取指定会话的向量索引统计
        """
        async for session in get_db_session():
            try:
                # 统计：
                # 1. Total Eligible Heads: 仅统计 "Head" (前一条不是 AI 的 AI 消息)
                # 2. Indexed Heads: 仅统计非 Zero Vector 的索引
                
                # 构造 Zero Vector JSON 字符串用于排除
                zero_vec_json = json.dumps([0.0] * 1536)
                
                # SQLite 复杂统计 (Count Heads)
                # 使用嵌套查询判断 "Is Head"
                # (h.role='assistant' AND (prev.role IS NULL OR prev.role != 'assistant'))
                
                # Total Heads (分母)
                stmt_total = text("""
                    SELECT COUNT(*) FROM history h
                    WHERE h.chat_id = :chat_id 
                      AND h.role = 'assistant'
                      AND h.content IS NOT NULL
                      AND h.content != ''
                      AND h.content NOT LIKE '[%: Processing...]'
                      AND (
                          SELECT role FROM history prev 
                          WHERE prev.chat_id = :chat_id AND prev.id < h.id 
                          ORDER BY prev.id DESC LIMIT 1
                      ) IS NOT 'assistant'
                """)
                
                # Indexed Heads (分子)
                # 使用 rag_status 表统计 (Status='HEAD')
                # 这代表真正产生向量并已被索引的消息
                stmt_indexed = text("""
                    SELECT COUNT(*) FROM rag_status
                    WHERE chat_id = :chat_id AND status = 'HEAD'
                """)
                
                # Execute
                res_total = await session.execute(stmt_total, {"chat_id": chat_id})
                total = res_total.scalar() or 0
                
                res_indexed = await session.execute(stmt_indexed, {"chat_id": chat_id})
                indexed = res_indexed.scalar() or 0
                
                # 检查冷却状态
                cooldown_left = 0
                if chat_id in self._sync_cooldowns:
                     passed = time.time() - self._sync_cooldowns[chat_id]
                     if passed < self.SYNC_COOLDOWN_SECONDS:
                         cooldown_left = int(self.SYNC_COOLDOWN_SECONDS - passed)
                
                return {
                    "total_eligible": total,
                    "indexed": indexed,
                    "cooldown_left": cooldown_left
                }
            except Exception as e:
                logger.error(f"RAG Stats failed: {e}")
                return {"error": str(e)}

rag_service = RagService()
