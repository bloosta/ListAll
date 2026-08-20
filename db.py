import aiosqlite
import os
from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "/app/data/tasks.db")

async def init_db():
    # Каталог для БД может отсутствовать (свежий том/локальный запуск)
    parent = os.path.dirname(DB_PATH)
    if parent:
        os.makedirs(parent, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER UNIQUE,
                first_name TEXT,
                tone_preset TEXT DEFAULT 'motivational',
                tone_custom TEXT,
                nudge_enabled INTEGER DEFAULT 1
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
                created_at TEXT DEFAULT (datetime('now')),
                nudge_sent_at TEXT DEFAULT NULL
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

        # Миграции для существующих баз
        for col, definition in [
            ("nudge_enabled", "INTEGER DEFAULT 1"),
            ("nudge_sent_at", "TEXT DEFAULT NULL"),
        ]:
            try:
                table = "users" if col == "nudge_enabled" else "tasks"
                await db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
                await db.commit()
            except Exception:
                pass

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

async def get_done_tasks(telegram_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT t.id, t.title, t.deadline
            FROM tasks t
            JOIN users u ON t.user_id = u.id
            WHERE u.telegram_id = ? AND t.is_done = 1 AND t.parent_id IS NULL
            ORDER BY t.id DESC
            LIMIT 20
        """, (telegram_id,))
        return await cursor.fetchall()

async def delete_done_task(task_id: int, telegram_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            DELETE FROM tasks WHERE id = ? AND is_done = 1
            AND user_id = (SELECT id FROM users WHERE telegram_id = ?)
        """, (task_id, telegram_id))
        await db.execute("DELETE FROM tasks WHERE parent_id = ?", (task_id,))
        await db.execute("DELETE FROM reminders WHERE task_id = ?", (task_id,))
        await db.commit()

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
        await db.execute("DELETE FROM reminders WHERE task_id = ?", (task_id,))
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
        # Удаляем старые неотправленные напоминания для этой задачи
        await db.execute("""
            DELETE FROM reminders WHERE task_id = ? AND user_id = ? AND is_sent = 0
        """, (task_id, user[0]))
        await db.execute("""
            INSERT INTO reminders (task_id, user_id, remind_at)
            VALUES (?, ?, ?)
        """, (task_id, user[0], remind_at))
        await db.commit()
        return True

async def get_active_reminder(task_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT remind_at FROM reminders
            WHERE task_id = ? AND is_sent = 0
            ORDER BY remind_at ASC LIMIT 1
        """, (task_id,))
        return await cursor.fetchone()

async def get_nudge_enabled(telegram_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT nudge_enabled FROM users WHERE telegram_id = ?", (telegram_id,)
        )
        row = await cursor.fetchone()
        return bool(row[0]) if row else True

async def set_nudge_enabled(telegram_id: int, enabled: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET nudge_enabled = ? WHERE telegram_id = ?",
            (1 if enabled else 0, telegram_id)
        )
        await db.commit()

async def get_stale_tasks_for_nudge(threshold_iso: str, cooldown_iso: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT t.id, t.title, u.telegram_id, u.tone_preset, u.tone_custom
            FROM tasks t
            JOIN users u ON t.user_id = u.id
            WHERE t.is_done = 0
              AND t.parent_id IS NULL
              AND u.nudge_enabled = 1
              AND t.created_at <= ?
              AND (t.nudge_sent_at IS NULL OR t.nudge_sent_at <= ?)
        """, (threshold_iso, cooldown_iso))
        return await cursor.fetchall()

async def mark_nudge_sent(task_id: int, sent_at: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE tasks SET nudge_sent_at = ? WHERE id = ?", (sent_at, task_id)
        )
        await db.commit()