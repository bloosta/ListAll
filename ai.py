import os
import re
import logging
from groq import AsyncGroq
from dotenv import load_dotenv

load_dotenv()
client = AsyncGroq(api_key=os.getenv("GROQ_KEY") or os.getenv("GROQ_API_KEY"))

# Модель задаётся через .env, чтобы не лезть в код, когда Groq выводит очередную из строя
MODEL = os.getenv("GROQ_MODEL", "qwen/qwen3.6-27b")
# Разбиение должно быть трезвым, ролевые сообщения — живыми
TEMP_SPLIT = float(os.getenv("GROQ_TEMP_SPLIT", "0.6"))
TEMP_STYLE = float(os.getenv("GROQ_TEMP_STYLE", "0.8"))

logger = logging.getLogger(__name__)

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.S)
_THINK_TAIL = re.compile(r"<think>.*", re.S)
# Модель иногда отказывается отвечать — по-английски. Такой текст пользователю не нужен
_REFUSAL = re.compile(
    r"^(i'?m sorry|i am sorry|i can'?t|i cannot|sorry, but|as an ai)", re.I
)


class AIUnavailable(Exception):
    """ИИ не ответил — вызывающий код сам решает, что показать пользователю."""


def _clean(text: str) -> str:
    """Убирает следы размышлений, markdown и обрамляющие кавычки."""
    text = _THINK_BLOCK.sub("", text or "")
    text = _THINK_TAIL.sub("", text)
    text = text.replace("**", "").replace("__", "")
    return text.strip().strip('"«»').strip()


async def _ask_groq(prompt: str, temperature: float, max_tokens: int = 700) -> str:
    """Один запрос к Groq. Пустой ответ — одна повторная попытка, потом AIUnavailable."""
    kwargs = {"reasoning_effort": "none"}  # иначе рассуждающие модели съедают весь лимит
    for attempt in (1, 2):
        try:
            response = await client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=temperature,
                **kwargs
            )
        except Exception as e:
            # Модель из .env может не знать про reasoning_effort — повторяем без него
            if kwargs and "reasoning_effort" in str(e):
                logger.info("Модель %s не поддерживает reasoning_effort, повтор без него", MODEL)
                kwargs = {}
                continue
            logger.error("Groq (%s) не ответил: %s: %s", MODEL, type(e).__name__, e)
            raise AIUnavailable(str(e)) from e

        text = _clean(response.choices[0].message.content)
        if text and not _REFUSAL.match(text):
            return text
        logger.warning(
            "Groq (%s) вернул %s (попытка %s)",
            MODEL, "отказ" if text else "пустой ответ", attempt
        )
    raise AIUnavailable("пустой ответ или отказ модели")


def _clean_line(line: str) -> str:
    """Убирает маркеры списка в начале строки, не трогая цифры внутри текста."""
    line = line.strip().lstrip("•*-–— \t")
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

    text = await _ask_groq(prompt, TEMP_SPLIT, max_tokens=800)
    subtasks = [s for s in (_clean_line(l) for l in text.split("\n")) if s][:7]
    if not subtasks:
        logger.error("Groq вернул пустой разбор для %r: %r", task_title, text)
        raise AIUnavailable("модель не вернула подзадач")
    return subtasks


def _character(tone_preset: str, tone_custom: str = None) -> str:
    if tone_custom:
        return tone_custom
    if tone_preset == "motivational":
        return "мотивирующий жизнеутверждающий тренер"
    if tone_preset == "humor":
        return "друг с чувством юмора, подбадривает с иронией"
    if tone_preset == "strict":
        return "строгий наставник, сухо но по делу"
    return "дружелюбный помощник"


# Общая часть промпта: держим персонажа в роли и не даём скатываться в пояснения
_ROLE_HEADER = ("Ты пишешь пользователю от лица персонажа. "
                "Полностью вживись в роль и не смягчай её.\nПерсонаж: {character}\n")
_ROLE_FOOTER = ("Пиши на грамотном русском, 2-3 предложения, живо и выдумчиво. "
                "Без markdown, без кавычек вокруг ответа, без пояснений — "
                "только сам текст сообщения.")


async def generate_encouragement(task_title: str, tone_preset: str, tone_custom: str = None) -> str:
    """Подбадривание. Никогда не бросает исключение — задача важнее красивой фразы."""
    prompt = (_ROLE_HEADER.format(character=_character(tone_preset, tone_custom)) +
              f'\nПользователь только что выполнил задачу: "{task_title}"\n'
              "Похвали его от лица персонажа, обыграй саму формулировку задачи.\n" +
              _ROLE_FOOTER)
    try:
        return await _ask_groq(prompt, TEMP_STYLE)
    except AIUnavailable:
        return "Отличная работа! 🎉"


async def generate_nudge(task_title: str, tone_preset: str, tone_custom: str = None) -> str:
    """Напоминание-подбадривание. Никогда не бросает исключение."""
    prompt = (_ROLE_HEADER.format(character=_character(tone_preset, tone_custom)) +
              f'\nЗадача пользователя давно висит невыполненной: "{task_title}"\n'
              "Напомни о ней от лица персонажа, обыграй саму формулировку задачи, "
              "намекни что пора бы ею заняться.\n" + _ROLE_FOOTER)
    try:
        return await _ask_groq(prompt, TEMP_STYLE)
    except AIUnavailable:
        return "Задача всё ещё ждёт — самое время ею заняться."
