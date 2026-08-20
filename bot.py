import logging
import os
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters, ConversationHandler
)
from dotenv import load_dotenv
from db import (init_db, upsert_user, add_task, get_tasks, get_done_tasks,
                delete_done_task, mark_done, get_task_by_id, get_subtasks,
                mark_subtask_done, mark_task_split, clear_subtasks, update_task,
                delete_task, add_reminder, get_active_reminder, set_tone, get_tone,
                get_nudge_enabled, set_nudge_enabled)
from ai import split_task, generate_encouragement, AIUnavailable
from admin import start_admin

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
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

def to_utc(dt_naive: datetime) -> str:
    """Переводит московское время в UTC и возвращает isoformat."""
    return dt_naive.replace(tzinfo=ZoneInfo("Europe/Moscow")) \
                   .astimezone(ZoneInfo("UTC")) \
                   .replace(tzinfo=None) \
                   .isoformat()

def utc_to_moscow(iso_str: str) -> str:
    """Переводит UTC isoformat в московское время для отображения."""
    try:
        dt = datetime.fromisoformat(iso_str).replace(tzinfo=ZoneInfo("UTC"))
        return dt.astimezone(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y %H:%M")
    except Exception:
        return iso_str

# ─── Построитель сообщения задачи ─────────────────────────
async def build_task_message(task_id: int):
    task = await get_task_by_id(task_id)
    if not task:
        return "Задача не найдена", InlineKeyboardMarkup([])

    tid, title, description, deadline, is_split = task
    subtasks = await get_subtasks(task_id)
    active_reminder = await get_active_reminder(task_id)

    text = f"📌 {title}"
    if description:
        text += f"\n📝 {description}"
    if deadline:
        try:
            dt = datetime.fromisoformat(deadline)
            text += f"\n📅 {dt.strftime('%d.%m.%Y %H:%M')}"
        except Exception:
            pass
    if active_reminder:
        text += f"\n🔔 {utc_to_moscow(active_reminder[0])}"

    if subtasks:
        text += "\n"
        for sub_id, sub_title, sub_done in subtasks:
            icon = "✅" if sub_done else "☐"
            text += f"\n{icon} {sub_title}"

    buttons = []
    for sub_id, sub_title, sub_done in subtasks:
        if not sub_done:
            label = sub_title[:35] + "…" if len(sub_title) > 35 else sub_title
            buttons.append([InlineKeyboardButton(
                f"✅ {label}", callback_data=f"subdone_{sub_id}_{task_id}"
            )])

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
        f"/history — выполненные задачи\n"
        f"/tone — тон подбадривания\n"
        f"/settings — настройки\n\n"
        f"💡 Просто напиши что угодно — и я создам задачу!"
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

# ─── /history ─────────────────────────────────────────────
async def history_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tid = update.effective_user.id
    tasks = await get_done_tasks(tid)
    if not tasks:
        await update.message.reply_text("Выполненных задач пока нет.")
        return
    await update.message.reply_text(f"✅ Выполненные задачи ({len(tasks)}):")
    for task_id, title, deadline in tasks:
        text = f"✅ {title}"
        if deadline:
            try:
                dt = datetime.fromisoformat(deadline)
                text += f"\n📅 {dt.strftime('%d.%m.%Y %H:%M')}"
            except Exception:
                pass
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🗑 Удалить", callback_data=f"del_done_{task_id}")
        ]])
        await update.message.reply_text(text, reply_markup=keyboard)

# ─── /settings ────────────────────────────────────────────
async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tid = update.effective_user.id
    nudge = await get_nudge_enabled(tid)
    nudge_label = "🔔 Напоминалки: вкл" if nudge else "🔕 Напоминалки: выкл"
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(nudge_label, callback_data="nudge_toggle")
    ]])
    await update.message.reply_text(
        "⚙️ Настройки\n\n"
        "Автоматические напоминалки — бот пишет, если задача долго висит невыполненной.",
        reply_markup=keyboard
    )

# ─── Главный обработчик кнопок ────────────────────────────
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tid = update.effective_user.id
    data = query.data

    # Удаление выполненной задачи из истории
    if data.startswith("del_done_"):
        task_id = int(data.split("_")[2])
        await delete_done_task(task_id, tid)
        await query.edit_message_text("🗑 Удалено")
        return

    # Переключение автонапоминалок
    if data == "nudge_toggle":
        current = await get_nudge_enabled(tid)
        await set_nudge_enabled(tid, not current)
        new_label = "🔔 Напоминалки: вкл" if not current else "🔕 Напоминалки: выкл"
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(new_label, callback_data="nudge_toggle")
        ]])
        await query.edit_message_reply_markup(reply_markup=keyboard)
        return

    # Выполнено (главная задача)
    if data.startswith("done_"):
        task_id = int(data.split("_")[1])
        task = await get_task_by_id(task_id, tid)
        if not task:
            await query.edit_message_text("Задача не найдена — возможно, она уже удалена.")
            return
        await mark_done(task_id, tid)
        tone = await get_tone(tid)
        preset = tone[0] if tone else "motivational"
        custom = tone[1] if tone else None
        # generate_encouragement сам подставит запасной текст, если ИИ недоступен,
        # чтобы отметка "выполнено" не пропадала вместе с ошибкой
        encouragement = await generate_encouragement(task[1], preset, custom)
        try:
            await query.edit_message_text(f"✅ Выполнено: {task[1]}\n\n{encouragement}")
        except Exception as e:
            logging.warning("Не удалось обновить сообщение задачи %s: %s", task_id, e)

    # Выполнено (подзадача)
    elif data.startswith("subdone_"):
        parts = data.split("_")
        subtask_id = int(parts[1])
        task_id = int(parts[2])
        await mark_subtask_done(subtask_id, tid)
        text, keyboard = await build_task_message(task_id)
        try:
            await query.edit_message_text(text, reply_markup=keyboard)
        except Exception:
            pass

    # Разбить / Переразбить
    elif data.startswith("split_"):
        task_id = int(data.split("_")[1])
        task = await get_task_by_id(task_id, tid)
        if not task:
            await query.answer("Задача не найдена", show_alert=True)
            return
        try:
            await query.edit_message_text(f"🤖 Думаю над задачей «{task[1]}»...")
        except Exception:
            pass
        context_text = task[1]
        if task[2]:
            context_text += f". Контекст: {task[2]}"
        try:
            subtasks = await split_task(context_text)
        except AIUnavailable as e:
            # Старые подзадачи ещё на месте — возвращаем задачу в прежнем виде
            logging.error("Разбиение задачи %s не удалось: %s", task_id, e)
            text, keyboard = await build_task_message(task_id)
            await query.edit_message_text(
                text + "\n\n⚠️ ИИ сейчас недоступен, разбить не получилось. "
                "Попробуй позже или добавь подзадачу вручную через «Редактировать».",
                reply_markup=keyboard
            )
            return
        # Предыдущий разбор чистим только когда новый уже получен
        if task[4]:
            await clear_subtasks(task_id, tid)
        for sub in subtasks:
            await add_task(tid, sub, parent_id=task_id)
        await mark_task_split(task_id, tid)
        text, keyboard = await build_task_message(task_id)
        await query.edit_message_text(text, reply_markup=keyboard)

    # Меню редактирования
    elif data.startswith("editmenu_"):
        task_id = int(data.split("_")[1])
        task = await get_task_by_id(task_id, tid)
        if not task:
            await query.edit_message_text("Задача не найдена — возможно, она уже удалена.")
            return
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
        now = datetime.now(ZoneInfo("Europe/Moscow")).replace(tzinfo=None)

        if rtype == "1h":
            remind_at = to_utc(now + timedelta(hours=1))
            label = "через час"
        elif rtype == "eve":
            remind_at = to_utc(now.replace(hour=20, minute=0, second=0, microsecond=0))
            label = "сегодня в 20:00"
        elif rtype == "tom":
            remind_at = to_utc((now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0))
            label = "завтра в 09:00"
        elif rtype == "cust":
            context.user_data["remind_task_id"] = task_id
            context.user_data["awaiting_remind"] = True
            await query.edit_message_text(
                "Напиши дату и время напоминания:\nФормат: ДД.ММ.ГГГГ ЧЧ:ММ\n"
                "Например: 15.04.2026 09:00"
            )
            return
        else:
            return

        await add_reminder(task_id, tid, remind_at)
        await query.edit_message_text(
            f"⏰ Напоминание установлено — {label}\n📌 {task[1]}"
        )

# ─── Обработчик текста ────────────────────────────────────
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tid = update.effective_user.id

    if context.user_data.get("awaiting_tone"):
        context.user_data["awaiting_tone"] = False
        custom = update.message.text.strip()
        await set_tone(tid, preset="custom", custom=custom)
        await update.message.reply_text(f"Тон установлен: «{custom}»")
        return

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

        msg_text, keyboard = await build_task_message(task_id)
        await update.message.reply_text(msg_text, reply_markup=keyboard)
        return

    if context.user_data.get("awaiting_remind"):
        context.user_data["awaiting_remind"] = False
        task_id = context.user_data.get("remind_task_id")
        task = await get_task_by_id(task_id)
        text = update.message.text.strip()
        try:
            dt_moscow = datetime.strptime(text, "%d.%m.%Y %H:%M")
            remind_at = to_utc(dt_moscow)
            await add_reminder(task_id, tid, remind_at)
            await update.message.reply_text(
                f"⏰ Напоминание установлено: {text}\n📌 {task[1]}"
            )
        except ValueError:
            await update.message.reply_text("Неверный формат. Попробуй: 15.04.2026 09:00")
            context.user_data["awaiting_remind"] = True
        return

    # Быстрое добавление задачи
    await upsert_user(tid, update.effective_user.first_name)
    title = update.message.text.strip()
    task_id = await add_task(tid, title)
    if task_id:
        text, keyboard = await build_task_message(task_id)
        await update.message.reply_text(f"✅ Задача создана!\n\n{text}", reply_markup=keyboard)

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
        await query.edit_message_text("Напиши свой стиль:\n\nНапример: «Шрек — грубый, но добрый»")
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

async def error_handler(update, context: ContextTypes.DEFAULT_TYPE):
    import traceback
    from telegram.error import NetworkError, TimedOut
    if isinstance(context.error, (NetworkError, TimedOut)):
        logging.warning(f"Сетевая ошибка: {context.error}")
        return
    logging.error("Ошибка: %s", context.error)
    logging.error(traceback.format_exc())
    if update and update.effective_message:
        try:
            await update.effective_message.reply_text("⚠️ Что-то пошло не так. Попробуй ещё раз.")
        except Exception:
            pass

# ─── Запуск ───────────────────────────────────────────────
async def post_init(app):
    await init_db()
    from scheduler import start_scheduler
    start_scheduler(app.bot)
    start_admin()
    await app.bot.set_my_commands([
        ("start", "Главная"),
        ("add", "Добавить задачу"),
        ("list", "Мои задачи"),
        ("history", "Выполненные задачи"),
        ("tone", "Настроить тон подбадривания"),
        ("settings", "Настройки"),
    ])

if __name__ == "__main__":
    import time

    def build_app():
        application = (
            ApplicationBuilder()
            .token(BOT_TOKEN)
            .post_init(post_init)
            .connect_timeout(30)
            .read_timeout(30)
            .write_timeout(30)
            .build()
        )

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

        application.add_handler(add_conv)
        application.add_handler(tone_conv)
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("list", list_tasks))
        application.add_handler(CommandHandler("history", history_tasks))
        application.add_handler(CommandHandler("settings", settings))
        application.add_handler(CallbackQueryHandler(tone_button, pattern="^tone_"))
        application.add_handler(CallbackQueryHandler(button_handler))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
        application.add_error_handler(error_handler)
        return application


    while True:
        # Каждой попытке — свой цикл событий, старый обязательно закрываем:
        # иначе после первого падения PTB получает уже закрытый loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            print("Бот запущен!")
            build_app().run_polling(
                drop_pending_updates=True,
                allowed_updates=Update.ALL_TYPES,
            )
        except Exception as e:
            from telegram.error import NetworkError, TimedOut

            if isinstance(e, (NetworkError, TimedOut)):
                logging.warning(f"Сетевая ошибка, перезапуск через 3 сек: {e}")
                time.sleep(3)
            else:
                logging.exception(f"Бот упал: {e}. Перезапуск через 5 секунд...")
                time.sleep(5)
        finally:
            try:
                loop.close()
            except Exception:
                pass