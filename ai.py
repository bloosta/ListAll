import os
from groq import AsyncGroq
from dotenv import load_dotenv

load_dotenv()
client = AsyncGroq(api_key=os.getenv("GROQ_KEY"))
MODEL = "llama-3.3-70b-versatile"

async def _ask_groq(prompt: str) -> str:
    response = await client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1000,
        temperature=0.7
    )
    return response.choices[0].message.content.strip()

async def split_task(task_title: str) -> list[str]:
    prompt = f"""Разбей следующую задачу на конкретные подзадачи.
Задача: "{task_title}"

Правила:
- От 3 до 7 подзадач
- Каждая подзадача — одно конкретное действие
- Пиши кратко, одной строкой
- Отвечай ТОЛЬКО списком подзадач, каждая с новой строки
- Без нумерации, без тире, без лишних слов"""

    text = await _ask_groq(prompt)
    lines = text.strip().split("\n")
    return [line.strip("•-– 1234567890.").strip() for line in lines if line.strip()]

async def generate_encouragement(task_title: str, tone_preset: str, tone_custom: str = None) -> str:
    if tone_custom:
        style = tone_custom
    elif tone_preset == "motivational":
        style = "мотивирующий жизнеутверждающий тренер"
    elif tone_preset == "humor":
        style = "друг с чувством юмора, подбадривает с иронией"
    elif tone_preset == "strict":
        style = "строгий наставник, сухо но по делу"
    else:
        style = "дружелюбный помощник"

    prompt = f"""Пользователь выполнил задачу: "{task_title}"
Напиши короткое подбадривающее сообщение (1-2 предложения) в стиле: {style}
Только само сообщение, без лишних слов."""

    return await _ask_groq(prompt)

async def generate_nudge(task_title: str, tone_preset: str, tone_custom: str = None) -> str:
    if tone_custom:
        style = tone_custom
    elif tone_preset == "motivational":
        style = "мотивирующий тренер"
    elif tone_preset == "humor":
        style = "друг с юмором, слегка подтрунивает"
    elif tone_preset == "strict":
        style = "строгий наставник"
    else:
        style = "дружелюбный помощник"

    prompt = f"""Задача уже давно висит невыполненной: "{task_title}"
Напиши короткое напоминание-подбадривание (1-2 предложения) в стиле: {style}
Намекни что пора бы заняться этим. Только само сообщение, без лишних слов."""

    return await _ask_groq(prompt)