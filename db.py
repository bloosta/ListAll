import aiosqlite
import os

DB_PATH = "tasks.db"

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER UNIQUE,
                first_name TEXT,
                tone_preset TEXT DEFAULT 'motivational',
                tone_custom TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                parent_id INTEGER DEFAULT NULL,
                title TEXT,
                deadline TEXT,
                is_done INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER,
                user_id INTEGER,
                remind_at TEXT,
                is_sent INTEGER DEFAULT 0
            )
        """)
        await db.commit()

async def upsert_user(telegram_id: int, first_name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO users (telegram_id, first_name)
            VALUES (?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET first_name=excluded.first_name
        """, (telegram_id, first_name))
        await db.commit()

async def add_task(telegram_id: int, title: str, deadline: str = None, parent_id: int = None):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id FROM users WHERE telegram_id = ?", (telegram_id,)
        )
        user = await cursor.fetchone()
        if not user:
            return None
        await db.execute("""
            INSERT INTO tasks (user_id, title, deadline, parent_id)
            VALUES (?, ?, ?, ?)
        """, (user[0], title, deadline, parent_id))
        await db.commit()
        cursor = await db.execute("SELECT last_insert_rowid()")
        row = await cursor.fetchone()
        return row[0]

async def get_tasks(telegram_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT t.id, t.title, t.deadline, t.is_done, t.parent_id
            FROM tasks t
            JOIN users u ON t.user_id = u.id
            WHERE u.telegram_id = ? AND t.is_done = 0
            ORDER BY t.parent_id NULLS FIRST, t.id
        """, (telegram_id,))
        return await cursor.fetchall()

async def mark_done(task_id: int, telegram_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE tasks SET is_done = 1
            WHERE id = ? AND user_id = (
                SELECT id FROM users WHERE telegram_id = ?
            )
        """, (task_id, telegram_id))
        await db.commit()

async def get_task_by_id(task_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, title, deadline FROM tasks WHERE id = ?", (task_id,)
        )
        return await cursor.fetchone()

async def get_tone(telegram_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT tone_preset, tone_custom FROM users WHERE telegram_id = ?",
            (telegram_id,)
        )
        return await cursor.fetchone()

async def set_tone(telegram_id: int, preset: str = None, custom: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE users SET tone_preset = ?, tone_custom = ?
            WHERE telegram_id = ?
        """, (preset, custom, telegram_id))
        await db.commit()