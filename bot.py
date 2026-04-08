import logging
import os
import asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters, ConversationHandler
)
from dotenv import load_dotenv
from db import (init_db, upsert_user, add_task, get_tasks, mark_done,
                get_task_by_id, get_subtasks, mark_subtask_done, mark_task_split,
                clear_subtasks, update_task, delete_task,
                add_reminder, set_tone, get_tone)
from ai import split_task, generate_encouragement
from scheduler import run_scheduler

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# Состояния
WAITING_TITLE = 0
WAITING_DESCRIPTION = 1
WAITING_DEADLINE = 2
EDIT_CHOICE = 3
EDIT_VALUE = 4
WAITING_TONE_CUSTOM = 10
WAITING_REMIND_CUSTOM = 11
WAITING_MANUAL_SUBTASK = 12

# ─── Построитель сообщения задачи ─────────────────────────
async def build_task_message(task_id: int):
    task = await get_task_by_id(task_id)
    if not task:
        return "Задача не найдена", InlineKeyboardMarkup([])

    tid, title, description, deadline, is_split = task
    subtasks = await get_subtasks(task_id)

    # Текст сообщения
    text = f"📌 {title}"
    if description:
        text += f"\n📝 {description}"
    if deadline:
        try:
            dt = datetime.fromisoformat(deadline)
            text += f"\n📅 {dt.strftime('%d.%m.%Y %H:%M')}"
        except:
            pass

    if subtasks:
        text += "\n"
        for sub_id, sub_title, sub_done in subtasks:
            icon = "✅" if sub_done else "☐"
            text += f"\n{icon} {sub_title}"

    # Кнопки подзадач (только незавершённые)
    buttons = []
    for sub_id, sub_title, sub_done in subtasks:
        if not sub_done:
            label = sub_title[:35] + "…" if len(sub_title) > 35 else sub_title
            buttons.append([InlineKeyboardButton(
                f"✅ {label}", callback_data=f"subdone_{sub_id}_{task_id}"
            )])

    # Основные кнопки
    split_label = "🔄 Переразбить" if is_split else "🤖 Разбить"
    buttons.append([
        InlineKeyboardButton("✅ Выполнено", callback_data=f"done_{task_id}"),
        InlineKeyboardButton(split_label, callback_data=f"split_{task_id}")
    ])
    buttons.append([
        InlineKeyboardButton("✏️ Редактировать", callback_data=f"editmenu_{task_id}"),
        InlineKeyboardButton("⏰ Напоминание", callback_data=f"remindmenu_{task_id}")
    ])

    return text, InlineKeyboardMarkup(buttons)

# ─── /start ───────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name
    await upsert_user(update.effective_user.id, name)
    await update.message.reply_text(
        f"Привет, {name}! 👋\n\n"
        f"Я твой менеджер задач:\n\n"
        f"/add — добавить задачу\n"
        f"/list — список задач\n"
        f"/tone — настроить тон подбадривания"
    )

# ─── /add ─────────────────────────────────────────────────
async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await upsert_user(update.effective_user.id, update.effective_user.first_name)
    await update.message.reply_text("Напиши название задачи:")
    return WAITING_TITLE

async def add_get_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["task_title"] = update.message.text
    await update.message.reply_text(
        "Добавь описание задачи (необязательно):\n\n"
        "Оно поможет ИИ точнее разбить задачу на подзадачи.\n"
        "Или /skip чтобы пропустить"
    )
    return WAITING_DESCRIPTION

async def add_get_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["task_desc"] = update.message.text
    await update.message.reply_text(
        "Укажи дедлайн в формате ДД.ММ.ГГГГ ЧЧ:ММ\n"
        "Например: 15.04.2026 18:00\n\n"
        "Или /skip чтобы пропустить"
    )
    return WAITING_DEADLINE

async def add_skip_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["task_desc"] = None
    await update.message.reply_text(
        "Укажи дедлайн в формате ДД.ММ.ГГГГ ЧЧ:ММ\n"
        "Например: 15.04.2026 18:00\n\n"
        "Или /skip чтобы пропустить"
    )
    return WAITING_DEADLINE

async def add_get_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    deadline_text = update.message.text.strip()
    try:
        deadline = datetime.strptime(deadline_text, "%d.%m.%Y %H:%M").isoformat()
    except ValueError:
        await update.message.reply_text("Неверный формат. Попробуй: 15.04.2026 18:00")
        return WAITING_DEADLINE
    await _save_task(update, context, deadline)
    return ConversationHandler.END

async def add_skip_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _save_task(update, context, None)
    return ConversationHandler.END

async def _save_task(update, context, deadline):
    tid = update.effective_user.id
    title = context.user_data.get("task_title")
    desc = context.user_data.get("task_desc")
    task_id = await add_task(tid, title, deadline, description=desc)
    text, keyboard = await build_task_message(task_id)
    await update.message.reply_text(f"Задача добавлена!\n\n{text}", reply_markup=keyboard)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END

# ─── /list ────────────────────────────────────────────────
async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tid = update.effective_user.id
    await upsert_user(tid, update.effective_user.first_name)
    tasks = await get_tasks(tid)
    if not tasks:
        await update.message.reply_text("У тебя пока нет задач! Добавь первую: /add")
        return
    for task in tasks:
        task_id = task[0]
        text, keyboard = await build_task_message(task_id)
        await update.message.reply_text(text, reply_markup=keyboard)

# ─── Главный обработчик кнопок ────────────────────────────
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tid = update.effective_user.id
    data = query.data

    # Выполнено (главная задача)
    if data.startswith("done_"):
        task_id = int(data.split("_")[1])
        task = await get_task_by_id(task_id)
        await mark_done(task_id, tid)
        tone = await get_tone(tid)
        preset = tone[0] if tone else "motivational"
        custom = tone[1] if tone else None
        encouragement = await generate_encouragement(task[1], preset, custom)
        await query.edit_message_text(f"✅ Выполнено: {task[1]}\n\n{encouragement}")

    # Выполнено (подзадача)
    elif data.startswith("subdone_"):
        parts = data.split("_")
        subtask_id = int(parts[1])
        task_id = int(parts[2])
        await mark_subtask_done(subtask_id)
        text, keyboard = await build_task_message(task_id)
        try:
            await query.edit_message_text(text, reply_markup=keyboard)
        except Exception:
            pass

    # Разбить / Переразбить
    elif data.startswith("split_"):
        task_id = int(data.split("_")[1])
        task = await get_task_by_id(task_id)
        try:
            await query.edit_message_text(f"🤖 Думаю над задачей «{task[1]}»...")
        except Exception:
            pass
        if task[4]:  # is_split — переразбивка
            await clear_subtasks(task_id)
        # Передаём описание в ИИ если есть
        context_text = task[1]
        if task[2]:
            context_text += f". Контекст: {task[2]}"
        subtasks = await split_task(context_text)
        for sub in subtasks:
            await add_task(tid, sub, parent_id=task_id)
        await mark_task_split(task_id)
        text, keyboard = await build_task_message(task_id)
        await query.edit_message_text(text, reply_markup=keyboard)

    # Меню редактирования
    elif data.startswith("editmenu_"):
        task_id = int(data.split("_")[1])
        context.user_data["edit_task_id"] = task_id
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ Название", callback_data="editf_title"),
             InlineKeyboardButton("📝 Описание", callback_data="editf_desc")],
            [InlineKeyboardButton("📅 Дедлайн", callback_data="editf_deadline"),
             InlineKeyboardButton("➕ Подзадача", callback_data="editf_subtask")],
            [InlineKeyboardButton("🗑 Удалить задачу", callback_data="editf_delete")]
        ])
        await query.edit_message_text("Что хочешь изменить?", reply_markup=keyboard)

    # Поле редактирования выбрано
    elif data.startswith("editf_"):
        field = data.split("_")[1]
        task_id = context.user_data.get("edit_task_id")
        if field == "delete":
            await delete_task(task_id, tid)
            await query.edit_message_text("🗑 Задача удалена")
            return
        context.user_data["edit_field"] = field
        prompts = {
            "title": "Напиши новое название:",
            "desc": "Напиши новое описание (или /skip чтобы убрать):",
            "deadline": "Напиши новый дедлайн (ДД.ММ.ГГГГ ЧЧ:ММ) или /skip чтобы убрать:",
            "subtask": "Напиши название новой подзадачи:"
        }
        await query.edit_message_text(prompts[field])
        context.user_data["awaiting_edit"] = True

    # Меню напоминания
    elif data.startswith("remindmenu_"):
        task_id = int(data.split("_")[1])
        context.user_data["remind_task_id"] = task_id
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⚡ Через час", callback_data=f"rset_1h_{task_id}"),
             InlineKeyboardButton("🌆 Сегодня в 20:00", callback_data=f"rset_eve_{task_id}")],
            [InlineKeyboardButton("🌅 Завтра в 09:00", callback_data=f"rset_tom_{task_id}"),
             InlineKeyboardButton("✏️ Своё время", callback_data=f"rset_cust_{task_id}")]
        ])
        await query.edit_message_text("Когда напомнить?", reply_markup=keyboard)

    # Установить напоминание
    elif data.startswith("rset_"):
        parts = data.split("_")
        rtype = parts[1]
        task_id = int(parts[2])
        task = await get_task_by_id(task_id)
        now = datetime.now()

        if rtype == "1h":
            remind_at = (now + timedelta(hours=1)).isoformat()
            label = "через час"
        elif rtype == "eve":
            remind_at = now.replace(hour=20, minute=0, second=0).isoformat()
            label = "сегодня в 20:00"
        elif rtype == "tom":
            remind_at = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0).isoformat()
            label = "завтра в 09:00"
        elif rtype == "cust":
            context.user_data["remind_task_id"] = task_id
            context.user_data["awaiting_remind"] = True
            await query.edit_message_text(
                "Напиши дату и время напоминания:\nФормат: ДД.ММ.ГГГГ ЧЧ:ММ\n"
                "Например: 15.04.2026 09:00"
            )
            return

        await add_reminder(task_id, tid, remind_at)
        await query.edit_message_text(
            f"⏰ Напоминание установлено — {label}\n📌 {task[1]}"
        )

# ─── Обработчик текста (для редактирования и напоминания) ─
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tid = update.effective_user.id


    if context.user_data.get("awaiting_tone"):
        context.user_data["awaiting_tone"] = False
        custom = update.message.text.strip()
        await set_tone(tid, preset="custom", custom=custom)
        await update.message.reply_text(f"Тон установлен: «{custom}»")
        return

    # Ожидаем ввод для редактирования
    if context.user_data.get("awaiting_edit"):
        context.user_data["awaiting_edit"] = False
        task_id = context.user_data.get("edit_task_id")
        field = context.user_data.get("edit_field")
        text = update.message.text.strip()

        if field == "title":
            await update_task(task_id, tid, title=text)
            await update.message.reply_text("✅ Название обновлено")
        elif field == "desc":
            await update_task(task_id, tid, description=text)
            await update.message.reply_text("✅ Описание обновлено")
        elif field == "deadline":
            try:
                deadline = datetime.strptime(text, "%d.%m.%Y %H:%M").isoformat()
                await update_task(task_id, tid, deadline=deadline)
                await update.message.reply_text(f"✅ Дедлайн: {text}")
            except ValueError:
                await update.message.reply_text("Неверный формат. Попробуй: 15.04.2026 18:00")
                context.user_data["awaiting_edit"] = True
                return
        elif field == "subtask":
            await add_task(tid, text, parent_id=task_id)
            await update.message.reply_text(f"✅ Подзадача добавлена: {text}")

        # Показываем обновлённую задачу
        msg_text, keyboard = await build_task_message(task_id)
        await update.message.reply_text(msg_text, reply_markup=keyboard)
        return

    # Ожидаем своё время напоминания
    if context.user_data.get("awaiting_remind"):
        context.user_data["awaiting_remind"] = False
        task_id = context.user_data.get("remind_task_id")
        task = await get_task_by_id(task_id)
        text = update.message.text.strip()
        try:
            remind_at = datetime.strptime(text, "%d.%m.%Y %H:%M").isoformat()
            await add_reminder(task_id, tid, remind_at)
            await update.message.reply_text(
                f"⏰ Напоминание установлено: {text}\n📌 {task[1]}"
            )
        except ValueError:
            await update.message.reply_text("Неверный формат. Попробуй: 15.04.2026 09:00")
            context.user_data["awaiting_remind"] = True
        return

    # Ожидаем /skip для описания или дедлайна при пропуске
    if context.user_data.get("skip_deadline"):
        context.user_data["skip_deadline"] = False

# ─── /tone ────────────────────────────────────────────────
async def tone_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💪 Мотивация", callback_data="tone_motivational"),
         InlineKeyboardButton("😄 Юмор", callback_data="tone_humor")],
        [InlineKeyboardButton("😤 Строгий", callback_data="tone_strict"),
         InlineKeyboardButton("✏️ Свой стиль", callback_data="tone_custom")]
    ])
    tone = await get_tone(update.effective_user.id)
    current = tone[1] if tone and tone[1] else (tone[0] if tone else "motivational")
    await update.message.reply_text(f"Текущий тон: {current}\n\nВыбери новый:", reply_markup=keyboard)

async def tone_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tid = update.effective_user.id
    data = query.data
    if data == "tone_custom":
        await query.edit_message_text("Напиши свой стиль:\n\nНапример: «будь как Шрек — грубо но по-доброму»")
        context.user_data["awaiting_tone"] = True
        return WAITING_TONE_CUSTOM
    preset = data.replace("tone_", "")
    await set_tone(tid, preset=preset, custom=None)
    names = {"motivational": "💪 Мотивация", "humor": "😄 Юмор", "strict": "😤 Строгий"}
    await query.edit_message_text(f"Тон установлен: {names.get(preset, preset)}")
    return ConversationHandler.END

async def tone_custom_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await set_tone(update.effective_user.id, preset="custom", custom=update.message.text.strip())
    await update.message.reply_text(f"Тон установлен: «{update.message.text.strip()}»")
    return ConversationHandler.END

# ─── Запуск ───────────────────────────────────────────────
async def post_init(app):
    await init_db()
    asyncio.create_task(run_scheduler(app.bot))

if __name__ == "__main__":
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    add_conv = ConversationHandler(
        entry_points=[CommandHandler("add", add_start)],
        states={
            WAITING_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_get_title)],
            WAITING_DESCRIPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_get_description),
                CommandHandler("skip", add_skip_description)
            ],
            WAITING_DEADLINE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_get_deadline),
                CommandHandler("skip", add_skip_deadline)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    tone_conv = ConversationHandler(
        entry_points=[CommandHandler("tone", tone_start)],
        states={
            WAITING_TONE_CUSTOM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, tone_custom_input),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False
    )

    app.add_handler(add_conv)
    app.add_handler(tone_conv)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("list", list_tasks))
    app.add_handler(CallbackQueryHandler(tone_button, pattern="^tone_"))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    print("Бот запущен!")
    app.run_polling(drop_pending_updates=True)