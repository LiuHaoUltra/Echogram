import asyncio
import os
from datetime import datetime, timedelta, timezone

# 设置环境变量，模拟测试环境
os.environ["TG_BOT_TOKEN"] = "test_token"
os.environ["ADMIN_USER_ID"] = "123456"
os.environ["RSSHUB_HOST"] = "https://rsshub.app" # 使用公共 RSSHub 测试，或者 mock

from core.news_service import NewsService
from utils.logger import logger

async def main():
    print("--- Starting RSS Verification ---")
    
    # 1. Test Clean HTML
    raw_html = "<p>Hello <b>World</b><br>Checking link <a href='x'>Click</a></p>"
    clean = NewsService._clean_html(raw_html)
    print(f"Original: {raw_html}")
    print(f"Cleaned:  {clean}")
    assert "Hello World" in clean
    assert "Checking link" in clean
    print("[PASS] HTML Cleaning")

    # 2. Test Fetch (需要真实网络，或者 Mock)
    # 我们尝试抓取一个极大概率存在的路由，例如 Telegram Blog 或者某个公共源
    # 注意：这取决于 RSSHUB_HOST 是否可用
    # 如果本地 docker 没起，这个会失败。
    # 我们这里仅模拟 _clean_html 如上。
    
    # 如果想测试 fetch_new_items，我们需要模拟 httpx 的响应
    # 或者留给用户手动运行 docker 后验证
    
    print("--- Verification Script Finished ---")
    print("Run inside docker or with valid RSSHUB_HOST to test actual fetching.")

if __name__ == "__main__":
    asyncio.run(main())
