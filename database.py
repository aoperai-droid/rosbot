# database.py
import aiosqlite
import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

DB_PATH = "posts.db"

async def init_db():
    """Инициализация базы данных"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                text TEXT NOT NULL,
                file_id TEXT,
                file_type TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                moderated_at TIMESTAMP,
                moderator_id INTEGER
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_user_id ON posts(user_id)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_status ON posts(status)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_created_at ON posts(created_at)
        """)
        await db.commit()

async def add_post(user_id: int, username: str, text: str, file_id: str = None, file_type: str = "text") -> int:
    """Добавление нового поста"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO posts (user_id, username, text, file_id, file_type) VALUES (?, ?, ?, ?, ?)",
            (user_id, username, text, file_id, file_type)
        )
        await db.commit()
        return cursor.lastrowid

async def get_post(post_id: int) -> Optional[Tuple]:
    """Получение поста по ID"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, user_id, username, text, file_id, file_type, status FROM posts WHERE id = ?",
            (post_id,)
        )
        return await cursor.fetchone()

async def update_status(post_id: int, status: str, moderator_id: int = None):
    """Обновление статуса поста"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE posts SET status = ?, moderated_at = ?, moderator_id = ? WHERE id = ?",
            (status, datetime.now(), moderator_id, post_id)
        )
        await db.commit()

async def get_today_stats() -> dict:
    """Статистика за сегодня для модераторов"""
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT status, COUNT(*) FROM posts WHERE created_at > ? GROUP BY status",
            (today,)
        )
        stats = await cursor.fetchall()
        return {status: count for status, count in stats}

async def clean_old_posts(days: int = 30):
    """Удаление старых постов"""
    cutoff = datetime.now() - timedelta(days=days)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM posts WHERE created_at < ? AND status != 'pending'",
            (cutoff,)
        )
        await db.commit()

async def get_pending_posts_count() -> int:
    """Возвращает количество постов на модерации"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM posts WHERE status = 'pending'"
        )
        row = await cursor.fetchone()
        return row[0] if row else 0