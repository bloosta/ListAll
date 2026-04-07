import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from dotenv import load_dotenv
import os

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name
    await update.message.reply_text(
        f"Привет, {name}! 👋\n\n"
        f"Я помогу тебе управлять задачами.\n\n"
        f"Команды:\n"
        f"• /add — добавить задачу\n"
        f"• /list — посмотреть список\n"
        f"• /done — отметить выполненной\n"
        f"• /split — разбить задачу на подзадачи с помощью ИИ\n"
        f"• /remind — поставить напоминание\n"
        f"• /tone — настроить тон подбадривания"
    )

if __name__ == "__main__":
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    print("Бот запущен!")
    app.run_polling()