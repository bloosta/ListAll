import logging
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters, ConversationHandler
)
from dotenv import load_dotenv
from db import init_db, upsert_user, add_task, get_tasks, mark_done, get_task_by_id
from ai import split_task, generate_encouragement
from db import get_tone

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# Состояния для диалога добавления задачи
WAITING_TITLE, WAITING_DEADLINE = range(2)

# /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name
    tid = update.effective_user.id
    await upsert_user(tid, name)
    await update.message.reply_text(
        f"Привет, {name}! 👋\n\n"
        f"Я твой менеджер задач. Вот что я умею:\n\n"
        f"/add — добавить задачу\n"
        f"/list — посмотреть список задач\n"
        f"/split [ID] — разбить задачу на подзадачи с помощью ИИ\n"
        f"/remind [ID] [время] — поставить напоминание\n"
        f"/tone — настроить тон подбадривания"
    )

# /add — начало диалога
async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Напиши название задачи:")
    return WAITING_TITLE

# Получили название — спрашиваем дедлайн
async def add_get_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["task_title"] = update.message.text
    await update.message.reply_text(
        "Теперь напиши дедлайн в формате ДД.ММ.ГГГГ ЧЧ:ММ\n"
        "Например: 15.04.2025 18:00\n\n"
        "Или напиши /skip чтобы пропустить"
    )
    return WAITING_DEADLINE

# Получили дедлайн — сохраняем задачу
async def add_get_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    deadline_text = update.message.text.strip()
    title = context.user_data.get("task_title")
    tid = update.effective_user.id

    # Проверяем формат даты
    from datetime import datetime
    try:
        deadline = datetime.strptime(deadline_text, "%d.%m.%Y %H:%M").isoformat()
    except ValueError:
        await update.message.reply_text(
            "Не понял формат. Попробуй ещё раз: ДД.ММ.ГГГГ ЧЧ:ММ\n"
            "Например: 15.04.2025 18:00"
        )
        return WAITING_DEADLINE

    task_id = await add_task(tid, title, deadline)
    await update.message.reply_text(
        f"Задача #{task_id} добавлена!\n"
        f"📌 {title}\n"
        f"📅 {deadline_text}"
    )
    return ConversationHandler.END

# Пропустить дедлайн
async def add_skip_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = context.user_data.get("task_title")
    tid = update.effective_user.id
    task_id = await add_task(tid, title)
    await update.message.reply_text(f"Задача #{task_id} добавлена без дедлайна!\n📌 {title}")
    return ConversationHandler.END

# Отмена
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END

# /list
async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tid = update.effective_user.id
    tasks = await get_tasks(tid)

    if not tasks:
        await update.message.reply_text("У тебя пока нет задач! Добавь первую: /add")
        return

    from datetime import datetime
    for task in tasks:
        task_id, title, deadline, is_done, parent_id = task

        # Форматируем дедлайн
        if deadline:
            try:
                dt = datetime.fromisoformat(deadline)
                deadline_str = dt.strftime("%d.%m.%Y %H:%M")
            except:
                deadline_str = deadline
        else:
            deadline_str = "без дедлайна"

        prefix = "  └ " if parent_id else "📌"
        text = f"{prefix} #{task_id} {title}\n📅 {deadline_str}"

        # Кнопки только для корневых задач
        if not parent_id:
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Выполнено", callback_data=f"done_{task_id}"),
                    InlineKeyboardButton("🤖 Разбить", callback_data=f"split_{task_id}")
                ]
            ])
            await update.message.reply_text(text, reply_markup=keyboard)
        else:
            await update.message.reply_text(text)

# Нажатие кнопки "Выполнено"
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tid = update.effective_user.id
    data = query.data

    if data.startswith("done_"):
        task_id = int(data.split("_")[1])
        task = await get_task_by_id(task_id)
        await mark_done(task_id, tid)

        # Подбадривание через ИИ
        tone = await get_tone(tid)
        preset = tone[0] if tone else "motivational"
        custom = tone[1] if tone else None
        encouragement = await generate_encouragement(task[1], preset, custom)

        await query.edit_message_text(f"✅ Выполнено: {task[1]}\n\n{encouragement}")

    elif data.startswith("split_"):
        task_id = int(data.split("_")[1])
        task = await get_task_by_id(task_id)

        # Игнорируем если сообщение уже изменено
        try:
            await query.edit_message_text(f"🤖 Думаю над задачей «{task[1]}»...")
        except Exception:
            pass

        subtasks = await split_task(task[1])

        for sub in subtasks:
            await add_task(tid, sub, parent_id=task_id)

        text = f"Разбил на {len(subtasks)} подзадач:\n\n"
        for i, sub in enumerate(subtasks, 1):
            text += f"{i}. {sub}\n"
        text += "\nСмотри /list чтобы увидеть их все"

        await query.edit_message_text(text)

# Запуск
async def post_init(app):
    await init_db()

if __name__ == "__main__":
    from telegram.ext import Application
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    # Диалог добавления задачи
    conv = ConversationHandler(
        entry_points=[CommandHandler("add", add_start)],
        states={
            WAITING_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_get_title)],
            WAITING_DEADLINE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_get_deadline),
                CommandHandler("skip", add_skip_deadline)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)
    app.add_handler(CommandHandler("list", list_tasks))
    app.add_handler(CallbackQueryHandler(button_handler))

    print("Бот запущен!")
    app.run_polling()