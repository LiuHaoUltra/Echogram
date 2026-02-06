
import asyncio
import sys
import os
from sqlalchemy import text
sys.path.append(os.getcwd())

from config.database import get_db_session

async def check_vectors():
    async for session in get_db_session():
        # Get Chat ID from args or default (User's ID from logs: -1003734019639)
        chat_id = -1003734019639
        
        # Global Counts
        print(f"\n--- Global Counts ---")
        c1 = (await session.execute(text("SELECT COUNT(*) FROM history"))).scalar()
        c2 = (await session.execute(text("SELECT COUNT(*) FROM history_vec"))).scalar()
        print(f"Total History: {c1}")
        print(f"Total Vectors: {c2}")

        # Chat Specific
        print(f"\n--- Chat Specific ({chat_id}) ---")
        c3 = (await session.execute(text("SELECT COUNT(*) FROM history WHERE chat_id=:cid"), {"cid": chat_id})).scalar()
        c4 = (await session.execute(text("SELECT COUNT(*) FROM history_vec v JOIN history h ON v.rowid=h.id WHERE h.chat_id=:cid"), {"cid": chat_id})).scalar()
        print(f"Chat History: {c3}")
        print(f"Chat Vectors: {c4}")

if __name__ == "__main__":
    asyncio.run(check_vectors())
