import os
import logging
from groq import AsyncGroq
from dotenv import load_dotenv

load_dotenv()
client = AsyncGroq(api_key=os.getenv("GROQ_KEY") or os.getenv("GROQ_API_KEY"))
# Модель задаётся через .env, чтобы не править код когда Groq выводит модель из строя
MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

logger = logging.getLogger(__name__)


class AIUnavailable(Exception):
    """ИИ не ответил — вызывающий код сам решает, что показать пользователю."""


async def _ask_groq(prompt: str) -> str:
    try:
        response = await client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1000,
            temperature=0.7
        )
    except Exception as e:
        logger.error("Groq (%s) не ответил: %s: %s", MODEL, type(e).__name__, e)
        raise AIUnavailable(str(e)) from e
    return (response.choices[0].message.content or "").strip()


def _clean_line(line: str) -> str:
    """Убирает маркеры списка в начале строки, не трогая цифры внутри текста."""
    line = line.strip()
    line = line.lstrip("•*-–— \t")
    # Ведущая нумерация вида "1." / "2)" — только в начале строки
    i = 0
    while i < len(line) and line[i].isdigit():
        i += 1
    if i and i < len(line) and line[i] in ".)":
        line = line[i + 1:]
    return line.strip(" \t*").strip()


async def split_task(task_title: str) -> list[str]:
    """Возвращает список подзадач. Бросает AIUnavailable, если ИИ недоступен."""
    prompt = f"""Разбей следующую задачу на конкретные подзадачи.
Задача: "{task_title}"

Правила:
- От 3 до 7 подзадач
- Каждая подзадача — одно конкретное действие
- Пиши кратко, одной строкой
- Отвечай ТОЛЬКО списком подзадач, каждая с новой строки
- Без нумерации, без тире, без лишних слов"""

    text = await _ask_groq(prompt)
    subtasks = [_clean_line(line) for line in text.split("\n")]
    subtasks = [s for s in subtasks if s][:7]
    if not subtasks:
        logger.error("Groq вернул пустой разбор для задачи %r: %r", task_title, text)
        raise AIUnavailable("пустой ответ модели")
    return subtasks


def _style(tone_preset: str, tone_custom: str = None, *, nudge: bool = False) -> str:
    if tone_custom:
        return tone_custom
    if tone_preset == "motivational":
        return "мотивирующий тренер" if nudge else "мотивирующий жизнеутверждающий тренер"
    if tone_preset == "humor":
        return "друг с юмором, слегка подтрунивает" if nudge else "друг с чувством юмора, подбадривает с иронией"
    if tone_preset == "strict":
        return "строгий наставник" if nudge else "строгий наставник, сухо но по делу"
    return "дружелюбный помощник"


async def generate_encouragement(task_title: str, tone_preset: str, tone_custom: str = None) -> str:
    """Подбадривание. Никогда не бросает исключение — задача важнее красивой фразы."""
    prompt = f"""Пользователь выполнил задачу: "{task_title}"
Напиши короткое подбадривающее сообщение (1-2 предложения) в стиле: {_style(tone_preset, tone_custom)}
Только само сообщение, без лишних слов."""
    try:
        return await _ask_groq(prompt)
    except AIUnavailable:
        return "Отличная работа! 🎉"


async def generate_nudge(task_title: str, tone_preset: str, tone_custom: str = None) -> str:
    """Напоминание-подбадривание. Никогда не бросает исключение."""
    prompt = f"""Задача уже давно висит невыполненной: "{task_title}"
Напиши короткое напоминание-подбадривание (1-2 предложения) в стиле: {_style(tone_preset, tone_custom, nudge=True)}
Намекни что пора бы заняться этим. Только само сообщение, без лишних слов."""
    try:
        return await _ask_groq(prompt)
    except AIUnavailable:
        return "Задача всё ещё ждёт — самое время ею заняться."
