import asyncio
import os
import sys

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from config.database import get_db_session
from utils.logger import logger

async def migrate_rag_status():
    print("Starting migration: Add denoised_content to rag_status...")
    
    async for session in get_db_session():
        try:
            # Check if column exists
            try:
                await session.execute(text("SELECT denoised_content FROM rag_status LIMIT 1"))
                print("Column 'denoised_content' already exists. Skipping.")
            except Exception:
                # Column likely doesn't exist, try to add it
                print("Column not found. Adding 'denoised_content'...")
                await session.execute(text("ALTER TABLE rag_status ADD COLUMN denoised_content TEXT"))
                await session.commit()
                print("Successfully added 'denoised_content' column.")

            # Verify schema
            result = await session.execute(text("PRAGMA table_info(rag_status)"))
            columns = result.fetchall()
            print("\nCurrent rag_status Setup:")
            for col in columns:
                print(f" - {col.name}: {col.type}")

        except Exception as e:
            print(f"Migration failed: {e}")
            await session.rollback()

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(migrate_rag_status())
