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
                # 2. 找出所有未嵌入的 AI 消息 (Anchors)
                stmt = text("""
                    SELECT h.id, h.role, h.content 
                    FROM history h
                    LEFT JOIN history_vec v ON h.id = v.rowid
                    WHERE h.chat_id = :chat_id 
                      AND h.role = 'assistant'
                      AND v.rowid IS NULL
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

                # 3. Context Fusion Loop
                items_to_embed = []
                valid_ids = []
                
                for ai_row in ai_rows:
                    ai_content = self.sanitize_content(ai_row.content)
                    if not ai_content: continue

                    # Lookback: 抓取最近的 User 消息 (最多 3 条连续)
                    # 抓取 5 条备选，然后在应用层截断，防止中间夹杂 System 消息
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
                                user_context_parts.insert(0, prev_content) # 插入到开头，保持时序
                                if len(user_context_parts) >= 3: # Max 3 context
                                    break
                        else:
                            # 遇到非 User 消息 (System/AI)，中断回溯，不仅是跳过，而是视为上一轮结束
                            break
                    
                    # 融合构建语义块
                    # Format: 
                    # User: ...
                    # Assistant: ...
                    
                    fused_text = ""
                    if user_context_parts:
                        user_block = " ".join(user_context_parts)
                        fused_text = f"User: {user_block}\nAssistant: {ai_content}"
                    else:
                        # Orphan AI (无上文)
                        fused_text = f"Assistant: {ai_content}"
                    
                    items_to_embed.append(fused_text)
                    valid_ids.append(ai_row.id)
                
                if not items_to_embed:
                    return

                # 4. 批量嵌入
                embeddings = await self._embed_texts(items_to_embed)
                
                # 5. [DEBUG] 通知超级管理员
                try:
                    import core.bot as bot_module
                    if bot_module.bot:
                        debug_msg = (
                            f"🔮 <b>RAG Sync: Interaction Mode</b>\n"
                            f"Chat: <code>{chat_id}</code> | Count: <code>{len(items_to_embed)}</code>\n"
                            f"<pre>{html.escape(items_to_embed[0][:200])}...</pre>"
                        )
                        # 仅发送第一条作为示例，避免刷屏
                        if len(items_to_embed) > 0:
                            await bot_module.bot.send_message(
                                chat_id=settings.ADMIN_USER_ID,
                                text=debug_msg,
                                parse_mode='HTML'
                            )
                except: pass

                # 6. 写入向量表
                for mid, vector in zip(valid_ids, embeddings):
                    await session.execute(
                        text("INSERT INTO history_vec(rowid, embedding) VALUES (:id, :embedding)"),
                        {"id": mid, "embedding": json.dumps(vector)} 
                    )
                
                await session.commit()
                logger.info(f"RAG Sync: Indexed {len(valid_ids)} interactions.")
                
                if chat_id in self._sync_cooldowns:
                    del self._sync_cooldowns[chat_id]

            except Exception as e:
                logger.error(f"RAG Sync failed for chat {chat_id}: {e}")
                self._sync_cooldowns[chat_id] = time.time()

    async def search_context(self, chat_id: int, query_text: str, exclude_ids: Optional[List[int]] = None, top_k: int = 5, context_padding: int = 2) -> str:
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
                    f"🔍 <b>RAG Search: Context Mode</b>\n"
                    f"Chat: <code>{chat_id}</code> | Q: <code>{html.escape(sanitized_query)}</code>\n"
                    f"TopK: {limit} | Pad: {context_padding}"
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
                    pre_res = await session.execute(pre_sql, {"cid": chat_id, "aid": anchor_id, "pad": context_padding})
                    pre_ids = [r.id for r in pre_res.fetchall()]
                    
                    # 获取后文 (Post-context)
                    post_sql = text("""
                        SELECT id FROM history 
                        WHERE chat_id = :cid AND id > :aid 
                        ORDER BY id ASC LIMIT :pad
                    """)
                    post_res = await session.execute(post_sql, {"cid": chat_id, "aid": anchor_id, "pad": context_padding})
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
                            f"✅ <b>RAG Context: Constructed</b>\n"
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
                # 统计：符合索引条件的非空消息总数 vs 已索引数量
                # 排除系统占位符
                stmt = text("""
                    SELECT 
                        COUNT(h.id) as total,
                        COUNT(v.rowid) as indexed
                    FROM history h
                    LEFT JOIN history_vec v ON h.id = v.rowid
                    WHERE h.chat_id = :chat_id
                      AND h.content IS NOT NULL
                      AND h.content != ''
                      AND h.content NOT LIKE '[%: Processing...]'
                """)
                
                result = await session.execute(stmt, {"chat_id": chat_id})
                row = result.fetchone()
                total = row.total if row else 0
                indexed = row.indexed if row else 0
                
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
