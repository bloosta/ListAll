import asyncio
import threading
from datetime import datetime
from db import DB_PATH
import aiosqlite

def start_scheduler(bot):
    thread = threading.Thread(target=_run_loop, args=(bot,), daemon=True)
    thread.start()

def _run_loop(bot):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_scheduler_loop(bot))

async def _scheduler_loop(bot):
    while True:
        try:
            await check_reminders(bot)
        except Exception as e:
            print(f"Ошибка планировщика: {e}")
        await asyncio.sleep(60)

async def check_reminders(bot):
    now = datetime.now().strftime("%Y-%m-%dT%H:%M")
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT r.id, r.user_id, r.task_id, t.title, u.telegram_id, u.tone_preset, u.tone_custom
            FROM reminders r
            JOIN tasks t ON r.task_id = t.id
            JOIN users u ON r.user_id = u.id
            WHERE r.is_sent = 0 AND r.remind_at <= ?
        """, (now,))
        rows = await cursor.fetchall()

        for row in rows:
            reminder_id, user_id, task_id, title, telegram_id, tone_preset, tone_custom = row
            try:
                from ai import generate_encouragement
                encouragement = await generate_encouragement(title, tone_preset, tone_custom)
                await bot.send_message(
                    chat_id=telegram_id,
                    text=f"⏰ Напоминание!\n\n📌 {title}\n\n{encouragement}"
                )
                await db.execute("UPDATE reminders SET is_sent = 1 WHERE id = ?", (reminder_id,))
            except Exception as e:
                print(f"Ошибка отправки напоминания {reminder_id}: {e}")

        await db.commit()