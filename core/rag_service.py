import re
import asyncio
from typing import List, Dict, Any
from sqlalchemy import select, text, and_
from openai import AsyncOpenAI
from config.settings import settings
from config.database import get_db_session
from core.config_service import config_service
from models.history import History
from utils.logger import logger

class RagService:
    def __init__(self):
        self._client = None
    
    async def _get_client(self):
        """获取或初始化 OpenAI Client"""
        if not self._client:
            configs = await config_service.get_all_settings()
            api_key = configs.get("api_key")
            base_url = configs.get("api_base_url")
            if not api_key:
                raise ValueError("API Key not configured")
            self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
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
            
            # 使用 text-embedding-3-small
            resp = await client.embeddings.create(
                input=texts,
                model=model_name
            )
            return [data.embedding for data in resp.data]
        except Exception as e:
            logger.error(f"Embedding API failed: {e}")
            raise

    async def sync_historic_embeddings(self, chat_id: int):
        """
        懒惰全量同步 (Lazy Full-Sync)
        查出该群组所有未嵌入的历史记录，批量生成并存入。
        """
        import json
        async for session in get_db_session():
            try:
                # 1. 找出所有未嵌入的 Text/Image/Voice (有实际内容的)
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
                    return

                logger.info(f"RAG Sync: Found {len(rows)} messages to embed for chat {chat_id}")

                # 2. 清洗与打包
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

                # 3. 批量嵌入
                embeddings = await self._embed_texts(items_to_embed)
                
                # 4. 写入向量表
                for mid, vector in zip(valid_ids, embeddings):
                    await session.execute(
                        text("INSERT INTO history_vec(rowid, embedding) VALUES (:id, :embedding)"),
                        # 🔥 Optimization: 使用 json.dumps 更稳健
                        {"id": mid, "embedding": json.dumps(vector)} 
                    )
                
                await session.commit()
                logger.info(f"RAG Sync: Successfully indexed {len(valid_ids)} messages.")
                
            except Exception as e:
                logger.error(f"RAG Sync failed for chat {chat_id}: {e}")
                # 不抛出异常，避免阻塞主流程

    async def search_context(self, chat_id: int, query_text: str, top_k: int = 5) -> str:
        """
        检索相关上下文
        """
        sanitized_query = self.sanitize_content(query_text)
        if len(sanitized_query) < 3:
            return ""

        try:
            # 1. 获取 Query Vector
            query_vecs = await self._embed_texts([sanitized_query])
            if not query_vecs:
                return ""
            query_vec = query_vecs[0]

            # 2. 向量检索 + JOIN
            async for session in get_db_session():
                stmt = text("""
                    SELECT 
                        h.role,
                        h.content, 
                        vec_distance_cosine(v.embedding, :query_vec) as distance,
                        h.timestamp
                    FROM history_vec v
                    JOIN history h ON v.rowid = h.id
                    WHERE h.chat_id = :chat_id 
                      AND distance < 0.6
                    ORDER BY distance ASC
                    LIMIT :top_k
                """)
                
                result = await session.execute(stmt, {
                    "query_vec": str(query_vec),
                    "chat_id": chat_id,
                    "top_k": top_k
                })
                rows = result.fetchall()
                
                if not rows:
                    return ""
                
                # 3. 格式化结果
                context_lines = []
                for row in rows:
                    # 再次清洗一下展示内容
                    content = self.sanitize_content(row.content)
                    date_str = row.timestamp.strftime("%Y-%m-%d") if row.timestamp else "Unknown"
                    context_lines.append(f"[{date_str}] {row.role.capitalize()}: {content}")
                
                return "\n".join(context_lines)

        except Exception as e:
            logger.error(f"RAG Search failed: {e}")
            return ""

rag_service = RagService()
