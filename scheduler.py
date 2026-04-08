import asyncio
from datetime import datetime
from db import DB_PATH
import aiosqlite

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
                print(f"Ошибка отправки напоминания: {e}")

        await db.commit()

async def run_scheduler(bot):
    while True:
        await check_reminders(bot)
        await asyncio.sleep(60)  # проверяем каждую минуту