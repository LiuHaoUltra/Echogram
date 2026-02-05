import re
import asyncio
import json
import time
from typing import List, Dict, Any, Optional
from sqlalchemy import select, text, and_
from openai import AsyncOpenAI
from config.settings import settings
from config.database import get_db_session
from core.config_service import config_service
from models.history import History
from utils.logger import logger

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
        懒惰全量同步 (Lazy Full-Sync)
        查出该群组所有未嵌入的历史记录，批量生成并存入。
        增加熔断机制：如果上次失败在冷却期内，则跳过。
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
            # 处于冷却期，静默跳过
            return

        async for session in get_db_session():
            try:
                # 2. 找出所有未嵌入的 Text/Image/Voice (有实际内容的)
                # 使用 NOT IN 查找 history_vec 中不存在的 id
                # 限制 50 条以防超时
                # 🔥 Fix: 增加 SQL 层过滤占位符，防止无限空转
                stmt = text("""
                    SELECT h.id, h.role, h.content 
                    FROM history h
                    LEFT JOIN history_vec v ON h.id = v.rowid
                    WHERE h.chat_id = :chat_id 
                      AND v.rowid IS NULL
                      AND h.content IS NOT NULL
                      AND h.content != ''
                      AND h.content NOT LIKE '[%: Processing...]'
                    LIMIT 50
                """)
                
                result = await session.execute(stmt, {"chat_id": chat_id})
                rows = result.fetchall()
                
                if not rows:
                    # 成功执行且无积压，清除可能的旧冷却记录（虽然非必须）
                    if chat_id in self._sync_cooldowns:
                        del self._sync_cooldowns[chat_id]
                    return

                logger.info(f"RAG Sync: Found {len(rows)} messages to embed for chat {chat_id}")

                # 3. 清洗与打包
                items_to_embed = []
                valid_ids = []
                
                for row in rows:
                    sanitized = self.sanitize_content(row.content)
                    # 🔥 Fix: 只要有语义就存，避免短消息导致的数据空洞
                    if sanitized and sanitized.strip():
                        # 拼接角色前缀，增加语义
                        full_text = f"{row.role.capitalize()}: {sanitized}"
                        items_to_embed.append(full_text)
                        valid_ids.append(row.id)
                
                if not items_to_embed:
                    return

                # 4. 批量嵌入
                embeddings = await self._embed_texts(items_to_embed)
                
                # 5. 写入向量表
                for mid, vector in zip(valid_ids, embeddings):
                    await session.execute(
                        text("INSERT INTO history_vec(rowid, embedding) VALUES (:id, :embedding)"),
                        # 🔥 Optimization: 使用 json.dumps 更稳健
                        {"id": mid, "embedding": json.dumps(vector)} 
                    )
                
                await session.commit()
                logger.info(f"RAG Sync: Successfully indexed {len(valid_ids)} messages.")
                
                # 成功后清除冷却记录
                if chat_id in self._sync_cooldowns:
                    del self._sync_cooldowns[chat_id]

            except Exception as e:
                logger.error(f"RAG Sync failed for chat {chat_id}: {e}")
                # 触发熔断
                self._sync_cooldowns[chat_id] = time.time()
                logger.warning(f"RAG Sync for chat {chat_id} entered cooldown for {cooldown}s.")

    async def search_context(self, chat_id: int, query_text: str, exclude_ids: Optional[List[int]] = None, top_k: int = 5) -> str:
        """
        检索相关上下文
        :param exclude_ids: 需要排除的消息 ID 列表 (避免自引用)
        """
        sanitized_query = self.sanitize_content(query_text)
        if len(sanitized_query) < 3:
            return ""

        # 使用默认或传入的 top_k (如果传入为 None/0 则用默认)
        limit = top_k if top_k else self.DEFAULT_TOP_K
        
        configs = await config_service.get_all_settings()
        threshold = self.DEFAULT_SIMILARITY_THRESHOLD
        try:
            if val := configs.get("rag_similarity_threshold"):
                threshold = float(val)
        except: pass

        # 构建 ID 排除条件
        exclusion_clause = ""
        params = {
            "chat_id": chat_id,
            "top_k": limit,
            "threshold": threshold
        }
        
        if exclude_ids:
            # 动态构建 NOT IN (:id1, :id2...) 过于复杂，改用 NOT IN 列表参数化
            # SQLAlchemy text 支持绑定列表
            exclusion_clause = "AND h.id NOT IN :exclude_ids"
            params["exclude_ids"] = tuple(exclude_ids) # 转换为 tuple

        try:
            # 1. 获取 Query Vector
            query_vecs = await self._embed_texts([sanitized_query])
            if not query_vecs:
                return ""
            query_vec = query_vecs[0]
            
            # 使用 json.dumps 确保格式安全
            params["query_vec"] = json.dumps(query_vec)

            # 2. 向量检索 + JOIN
            # 注意: vec_distance_cosine 越小越相似 (1 - cosine_similarity) ?
            # sqlite-vec 中 cosine_distance = 1.0 - cosine_similarity
            # 我们的阈值 0.6 原意可能是相似度 > 0.6 还是距离 < 0.6?
            # 原代码 distance < 0.6，意味着相似度 > 0.4，这是一个很宽泛的筛选。
            # 通常 embedding-3-small 的距离在 0.3-0.8 之间。
            # 假设原意是保留距离小于 0.6 的 (相似度 > 0.4)
            
            sql = f"""
                SELECT 
                    h.role,
                    h.content, 
                    vec_distance_cosine(v.embedding, :query_vec) as distance,
                    h.timestamp
                FROM history_vec v
                JOIN history h ON v.rowid = h.id
                WHERE h.chat_id = :chat_id 
                  AND distance < :threshold
                  {exclusion_clause}
                ORDER BY distance ASC
                LIMIT :top_k
            """
            
            async for session in get_db_session():
                stmt = text(sql)
                
                # 特殊处理列表参数绑定 (expanding=True)
                if exclude_ids:
                    from sqlalchemy import bindparam
                    stmt = stmt.bindparams(bindparam("exclude_ids", expanding=True))
                
                result = await session.execute(stmt, params)
                rows = result.fetchall()
                
                if not rows:
                    return ""
                
                # 3. 格式化结果
                context_lines = []
                for row in rows:
                    # 再次清洗一下展示内容
                    content = self.sanitize_content(row.content)
                    
                    # 兼容 timestamp 可能为 str (SQLite Raw SQL) 或 datetime
                    date_str = "Unknown"
                    if row.timestamp:
                        if hasattr(row.timestamp, 'strftime'):
                             date_str = row.timestamp.strftime("%Y-%m-%d")
                        else:
                             # 假设是字符串，取前10位 (YYYY-MM-DD)
                             date_str = str(row.timestamp)[:10]

                    context_lines.append(f"[{date_str}] {row.role.capitalize()}: {content}")
                
                return "\n".join(context_lines)

        except Exception as e:
            logger.error(f"RAG Search failed: {e}")
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
