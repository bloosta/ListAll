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
                description TEXT DEFAULT NULL,
                deadline TEXT,
                is_done INTEGER DEFAULT 0,
                is_split INTEGER DEFAULT 0,
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

async def add_task(telegram_id: int, title: str, deadline: str = None,
                   parent_id: int = None, description: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id FROM users WHERE telegram_id = ?", (telegram_id,)
        )
        user = await cursor.fetchone()
        if not user:
            return None
        await db.execute("""
            INSERT INTO tasks (user_id, title, description, deadline, parent_id)
            VALUES (?, ?, ?, ?, ?)
        """, (user[0], title, description, deadline, parent_id))
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
            WHERE u.telegram_id = ? AND t.is_done = 0 AND t.parent_id IS NULL
            ORDER BY t.id
        """, (telegram_id,))
        return await cursor.fetchall()

async def get_task_by_id(task_id: int, telegram_id: int = None):
    async with aiosqlite.connect(DB_PATH) as db:
        if telegram_id:
            cursor = await db.execute("""
                SELECT t.id, t.title, t.description, t.deadline, t.is_split
                FROM tasks t
                JOIN users u ON t.user_id = u.id
                WHERE t.id = ? AND u.telegram_id = ?
            """, (task_id, telegram_id))
        else:
            cursor = await db.execute(
                "SELECT id, title, description, deadline, is_split FROM tasks WHERE id = ?",
                (task_id,)
            )
        return await cursor.fetchone()

async def get_subtasks(task_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, title, is_done FROM tasks WHERE parent_id = ? ORDER BY id",
            (task_id,)
        )
        return await cursor.fetchall()

async def mark_done(task_id: int, telegram_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE tasks SET is_done = 1
            WHERE id = ? AND user_id = (SELECT id FROM users WHERE telegram_id = ?)
        """, (task_id, telegram_id))
        await db.execute("UPDATE tasks SET is_done = 1 WHERE parent_id = ?", (task_id,))
        await db.commit()

async def mark_subtask_done(subtask_id: int, telegram_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE tasks SET is_done = 1 WHERE id = ?
            AND user_id = (SELECT id FROM users WHERE telegram_id = ?)
        """, (subtask_id, telegram_id))
        await db.commit()

async def mark_task_split(task_id: int, telegram_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE tasks SET is_split = 1 WHERE id = ?
            AND user_id = (SELECT id FROM users WHERE telegram_id = ?)
        """, (task_id, telegram_id))
        await db.commit()

async def clear_subtasks(task_id: int, telegram_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            DELETE FROM tasks WHERE parent_id = ?
            AND (SELECT user_id FROM tasks WHERE id = ?) =
                (SELECT id FROM users WHERE telegram_id = ?)
        """, (task_id, task_id, telegram_id))
        await db.execute("""
            UPDATE tasks SET is_split = 0 WHERE id = ?
            AND user_id = (SELECT id FROM users WHERE telegram_id = ?)
        """, (task_id, telegram_id))
        await db.commit()

async def update_task(task_id: int, telegram_id: int, title: str = None,
                      description: str = None, deadline: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT title, description, deadline FROM tasks WHERE id = ?", (task_id,)
        )
        current = await cursor.fetchone()
        if not current:
            return False
        new_title = title if title is not None else current[0]
        new_desc = description if description is not None else current[1]
        new_deadline = deadline if deadline is not None else current[2]
        await db.execute("""
            UPDATE tasks SET title = ?, description = ?, deadline = ?
            WHERE id = ? AND user_id = (SELECT id FROM users WHERE telegram_id = ?)
        """, (new_title, new_desc, new_deadline, task_id, telegram_id))
        await db.commit()
        return True

async def delete_task(task_id: int, telegram_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            DELETE FROM tasks WHERE id = ? AND user_id = (
                SELECT id FROM users WHERE telegram_id = ?
            )
        """, (task_id, telegram_id))
        await db.execute("DELETE FROM tasks WHERE parent_id = ?", (task_id,))
        await db.commit()

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

async def add_reminder(task_id: int, telegram_id: int, remind_at: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id FROM users WHERE telegram_id = ?", (telegram_id,)
        )
        user = await cursor.fetchone()
        if not user:
            return False
        await db.execute("""
            INSERT INTO reminders (task_id, user_id, remind_at)
            VALUES (?, ?, ?)
        """, (task_id, user[0], remind_at))
        await db.commit()
        return True