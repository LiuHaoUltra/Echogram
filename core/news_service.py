import httpx
import re
import html
from datetime import datetime, timezone
from typing import List, Dict, Optional
from config.settings import settings
from utils.logger import logger

class NewsService:
    """
    新闻服务：负责从 RSSHub 获取并清洗数据
    """

    @staticmethod
    async def fetch_new_items(route: str, last_time: datetime) -> List[Dict]:
        """
        获取指定订阅源的新增条目
        :param route: RSSHub 路由 (e.g., /telegram/channel/tginfo)
        :param last_time: 上次读取的时间 (Naive UTC)
        :return: 清洗后的条目列表
        """
        url = f"{settings.RSSHUB_HOST}{route}"
        params = {"format": "json"}
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                data = response.json()
                
            items = data.get("items", [])
            new_items = []
            
            # 确保 last_time 有时区信息以便比较 (假设为 UTC)
            if last_time.tzinfo is None:
                last_time = last_time.replace(tzinfo=timezone.utc)

            for item in items:
                # 解析发布时间
                pub_date_str = item.get("date_published")
                if not pub_date_str:
                    continue
                
                try:
                    # 尝试解析 ISO 格式时间
                    # Python 3.11+ fromisoformat 处理能力增强，主要兼容 RSSHub 的标准输出
                    pub_date = datetime.fromisoformat(pub_date_str.replace('Z', '+00:00'))
                except ValueError:
                    logger.warning(f"Failed to parse date: {pub_date_str}")
                    continue

                if pub_date <= last_time:
                    continue

                # 清洗内容
                clean_content = NewsService._clean_html(item.get("content_html", ""))
                
                new_items.append({
                    "title": item.get("title", ""),
                    "content": clean_content,
                    "url": item.get("url", ""),
                    "date_published": pub_date
                })
            
            # 按时间正序排列 (旧 -> 新)
            new_items.sort(key=lambda x: x["date_published"])
            return new_items

        except httpx.HTTPError as e:
            logger.error(f"Error fetching news from {route}: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error in fetch_new_items: {e}")
            return []

    @staticmethod
    def _clean_html(raw_html: str) -> str:
        """
        移除 HTML 标签，仅保留文本
        """
        if not raw_html:
            return ""
        
        # 1. 解码 HTML 实体
        text = html.unescape(raw_html)
        
        # 2. 替换 <br> 和 <p> 为换行
        text = re.sub(r'<(br|p|div)[^>]*>', '\n', text)
        
        # 3. 移除所有其他标签
        text = re.sub(r'<[^>]+>', '', text)
        
        # 4. 去除多余空行和首尾空格
        text = re.sub(r'\n\s*\n', '\n', text).strip()
        
        return text
