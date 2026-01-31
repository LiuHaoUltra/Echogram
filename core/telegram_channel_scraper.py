import httpx
import re
import html
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from typing import List, Dict
from utils.logger import logger


class TelegramChannelScraper:
    """
    Telegram Channel Web 解析器
    直接解析公开频道页面，无需 RSSHub
    """

    @staticmethod
    async def scrape_channel(channel_username: str, last_time: datetime) -> List[Dict]:
        """
        抓取 Telegram Channel 公开页面的新消息
        :param channel_username: Channel 用户名 (如 tginfo)
        :param last_time: 上次读取的时间 (Naive UTC)
        :return: 新消息列表
        """
        url = f"https://t.me/s/{channel_username}"
        
        # 确保 last_time 有时区信息
        if last_time.tzinfo is None:
            last_time = last_time.replace(tzinfo=timezone.utc)
        
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                }
                response = await client.get(url, headers=headers)
                response.raise_for_status()
            
            logger.info(f"TelegramScraper: Fetched {len(response.text)} chars from {url}")
            
            # 解析 HTML
            soup = BeautifulSoup(response.text, 'lxml')
            messages = soup.select('.tgme_widget_message')
            
            logger.info(f"TelegramScraper: Found {len(messages)} total messages on page")
            
            new_items = []
            for msg in messages:
                # 提取时间
                time_elem = msg.select_one('.tgme_widget_message_date time')
                if not time_elem:
                    continue
                
                pub_time_str = time_elem.get('datetime')
                if not pub_time_str:
                    continue
                
                try:
                    pub_time = datetime.fromisoformat(pub_time_str.replace('Z', '+00:00'))
                except ValueError:
                    logger.warning(f"TelegramScraper: Failed to parse date: {pub_time_str}")
                    continue
                
                # 过滤旧消息
                if pub_time <= last_time:
                    continue
                
                # 提取消息 ID
                msg_id = msg.get('data-post', '').split('/')[-1]
                
                # 提取文本内容
                text_elem = msg.select_one('.tgme_widget_message_text')
                raw_content = ""
                if text_elem:
                    # 保留换行，转为纯文本
                    raw_content = TelegramChannelScraper._extract_text(text_elem)
                
                # 提取链接
                link_elem = msg.select_one('.tgme_widget_message_date')
                link = link_elem.get('href') if link_elem else ""
                
                # 提取标题（如果有）或使用内容前段
                title = raw_content[:100] if raw_content else f"Message {msg_id}"
                
                new_items.append({
                    "title": title,
                    "content": raw_content,
                    "url": link,
                    "date_published": pub_time
                })
            
            # 按时间正序排列
            new_items.sort(key=lambda x: x["date_published"])
            
            logger.info(f"TelegramScraper: Found {len(new_items)} new items from {channel_username}")
            return new_items
            
        except httpx.HTTPError as e:
            logger.error(f"TelegramScraper: HTTP error for {channel_username}: {e}")
            return []
        except Exception as e:
            logger.error(f"TelegramScraper: Unexpected error: {e}", exc_info=True)
            return []
    
    @staticmethod
    def _extract_text(element) -> str:
        """
        从 HTML 元素提取纯文本，保留基本格式
        """
        # 获取所有文本，保留换行
        text = element.get_text(separator='\n', strip=False)
        
        # 解码 HTML 实体
        text = html.unescape(text)
        
        # 清理多余空行
        text = re.sub(r'\n\s*\n+', '\n\n', text)
        
        # 去除首尾空格
        text = text.strip()
        
        return text
