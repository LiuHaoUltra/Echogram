import httpx
import re
import html
from datetime import datetime, timezone
from typing import List, Dict, Optional
from config.settings import settings
from utils.logger import logger
from core.telegram_channel_scraper import TelegramChannelScraper

class NewsService:
    """
    新闻服务：负责从 Telegram Channel 获取并清洗数据
    """

    @staticmethod
    async def fetch_new_items(route: str, last_time: datetime) -> List[Dict]:
        """
        获取指定 Telegram Channel 的新增条目
        :param route: Channel 用户名 (如 tginfo) 或旧格式路由 (/telegram/channel/tginfo)
        :param last_time: 上次读取的时间 (Naive UTC)
        :return: 清洗后的条目列表
        """
        # 兼容旧格式：提取 channel username
        channel_username = NewsService._extract_channel_username(route)
        
        try:
            # 使用新的 Telegram Channel Scraper
            new_items = await TelegramChannelScraper.scrape_channel(channel_username, last_time)
            logger.info(f"NewsService: Fetched {len(new_items)} new items from Channel: {channel_username}")
            return new_items

        except Exception as e:
            logger.error(f"NewsService: Unexpected error in fetch_new_items: {e}", exc_info=True)
            return []
    
    @staticmethod
    def _extract_channel_username(route: str) -> str:
        """
        从 route 提取 channel username
        兼容旧格式: /telegram/channel/tginfo -> tginfo
        新格式: tginfo -> tginfo
        """
        if route.startswith('/'):
            # 旧 RSSHub 格式: /telegram/channel/tginfo
            parts = route.strip('/').split('/')
            return parts[-1]  # 取最后一段
        else:
            # 新格式，直接就是 username
            return route.strip()

    @staticmethod
    def _clean_html(raw_html: str) -> str:
        """
        移除 HTML 标签，仅保留文本 (备用方法)
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
