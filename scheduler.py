import asyncio
import logging
import threading
from datetime import datetime, timezone, timedelta
from db import DB_PATH, get_stale_tasks_for_nudge, mark_nudge_sent
import aiosqlite

_scheduler_thread = None

def start_scheduler(bot):
    # post_init вызывается заново при каждом перезапуске бота — без этой
    # проверки после N падений накапливается N потоков и напоминания дублируются
    global _scheduler_thread
    if _scheduler_thread is not None and _scheduler_thread.is_alive():
        logging.info("Планировщик уже запущен, второй поток не создаём")
        return
    _scheduler_thread = threading.Thread(target=_run_loop, args=(bot,), daemon=True)
    _scheduler_thread.start()

def _run_loop(bot):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_scheduler_loop(bot))

async def _scheduler_loop(bot):
    tick = 0
    while True:
        try:
            await check_reminders(bot)
            if tick % 60 == 0:  # раз в час
                await check_nudges(bot)
            tick += 1
        except Exception as e:
            logging.exception("Ошибка планировщика: %s", e)
        await asyncio.sleep(60)

async def check_reminders(bot):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT r.id, r.user_id, r.task_id, t.title, u.telegram_id, u.tone_preset, u.tone_custom
            FROM reminders r
            JOIN tasks t ON r.task_id = t.id
            JOIN users u ON r.user_id = u.id
            WHERE r.is_sent = 0 AND r.remind_at <= ? AND t.is_done = 0
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
                await db.commit()
            except Exception as e:
                logging.exception("Ошибка отправки напоминания %s: %s", reminder_id, e)

        await db.commit()

async def check_nudges(bot):
    now_utc = datetime.now(timezone.utc)
    threshold = (now_utc - timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%S")
    cooldown = (now_utc - timedelta(hours=6)).isoformat()

    rows = await get_stale_tasks_for_nudge(threshold, cooldown)
    for row in rows:
        task_id, title, telegram_id, tone_preset, tone_custom = row
        try:
            from ai import generate_nudge
            nudge = await generate_nudge(title, tone_preset, tone_custom)
            await bot.send_message(
                chat_id=telegram_id,
                text=f"👀 Задача ждёт тебя:\n\n📌 {title}\n\n{nudge}"
            )
            await mark_nudge_sent(task_id, now_utc.isoformat())
        except Exception as e:
            logging.exception("Ошибка nudge для задачи %s: %s", task_id, e)