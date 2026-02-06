
import asyncio
import sys
import os
from sqlalchemy import text
sys.path.append(os.getcwd())

from config.database import get_db_session

async def check_content():
    async for session in get_db_session():
        chat_id = -1003734019639
        keyword = "TTS"
        
        print(f"Checking for keyword '{keyword}' in chat {chat_id}...")
        
        stmt = text("""
            SELECT id, role, content, timestamp 
            FROM history 
            WHERE chat_id = :cid AND content LIKE :kw
        """)
        
        res = await session.execute(stmt, {"cid": chat_id, "kw": f"%{keyword}%"})
        rows = res.fetchall()
        
        if not rows:
            print("No matches found.")
        else:
            print(f"Found {len(rows)} matches:")
            for r in rows:
                print(f"[{r.id}] {r.role}: {r.content[:50]}...")
        
        # Also check Vector count again
        c_vec = (await session.execute(text("SELECT COUNT(*) FROM history_vec v JOIN history h ON v.rowid=h.id WHERE h.chat_id=:cid"), {"cid": chat_id})).scalar()
        print(f"Total Vectors Indexed: {c_vec}")

if __name__ == "__main__":
    asyncio.run(check_content())
