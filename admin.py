import threading
import uvicorn
import os
import aiosqlite
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import HTMLResponse
import httpx

DB_PATH = "/app/data/tasks.db"
app = FastAPI()
security = HTTPBasic()

def check_auth(credentials: HTTPBasicCredentials = Depends(security)):
    if credentials.password != os.getenv("ADMIN_PASSWORD", "admin"):
        raise HTTPException(status_code=401, detail="Неверный пароль")
    return True

# ─── Юзеры ───────────────────────────────────────────────
@app.get("/users")
async def get_users(auth=Depends(check_auth)):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT u.telegram_id, u.first_name, u.tone_preset,
                   COUNT(CASE WHEN t.is_done = 0 AND t.parent_id IS NULL THEN 1 END) as active,
                   COUNT(CASE WHEN t.is_done = 1 AND t.parent_id IS NULL THEN 1 END) as done
            FROM users u
            LEFT JOIN tasks t ON t.user_id = u.id
            GROUP BY u.id
            ORDER BY u.id DESC
        """)
        rows = await cursor.fetchall()
        return [{"telegram_id": r[0], "name": r[1], "tone": r[2],
                 "active_tasks": r[3], "done_tasks": r[4]} for r in rows]

# ─── Задачи конкретного юзера ─────────────────────────────
@app.get("/users/{telegram_id}/tasks")
async def get_user_tasks(telegram_id: int, auth=Depends(check_auth)):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT t.id, t.title, t.deadline, t.is_done, t.created_at
            FROM tasks t
            JOIN users u ON t.user_id = u.id
            WHERE u.telegram_id = ? AND t.parent_id IS NULL
            ORDER BY t.is_done, t.id DESC
        """, (telegram_id,))
        rows = await cursor.fetchall()
        return [{"id": r[0], "title": r[1], "deadline": r[2],
                 "done": bool(r[3]), "created_at": r[4]} for r in rows]

# ─── Рассылка всем ────────────────────────────────────────
@app.post("/broadcast")
async def broadcast(request: Request, auth=Depends(check_auth)):
    body = await request.json()
    text = body.get("text", "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Текст не может быть пустым")

    token = os.getenv("BOT_TOKEN")
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT telegram_id FROM users")
        users = [r[0] for r in await cursor.fetchall()]

    ok, fail = 0, 0
    async with httpx.AsyncClient() as client:
        for uid in users:
            try:
                await client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": uid, "text": text}
                )
                ok += 1
            except Exception:
                fail += 1

    return {"sent": ok, "failed": fail}

# ─── Написать конкретному юзеру ───────────────────────────
@app.post("/send/{telegram_id}")
async def send_message(telegram_id: int, request: Request, auth=Depends(check_auth)):
    body = await request.json()
    text = body.get("text", "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Текст не может быть пустым")

    token = os.getenv("BOT_TOKEN")
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": telegram_id, "text": text}
        )
    if r.status_code != 200:
        raise HTTPException(status_code=500, detail="Telegram вернул ошибку")
    return {"ok": True}

# ─── Статистика ───────────────────────────────────────────
@app.get("/stats")
async def stats(auth=Depends(check_auth)):
    async with aiosqlite.connect(DB_PATH) as db:
        users = (await (await db.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
        active = (await (await db.execute(
            "SELECT COUNT(*) FROM tasks WHERE is_done=0 AND parent_id IS NULL")).fetchone())[0]
        done = (await (await db.execute(
            "SELECT COUNT(*) FROM tasks WHERE is_done=1 AND parent_id IS NULL")).fetchone())[0]
        reminders = (await (await db.execute(
            "SELECT COUNT(*) FROM reminders WHERE is_sent=0")).fetchone())[0]
    return {"users": users, "active_tasks": active,
            "done_tasks": done, "pending_reminders": reminders}

# ─── Простой HTML-интерфейс ───────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def dashboard(auth=Depends(check_auth)):
    return """
    <html><head><title>Bot Admin</title></head><body>
    <h2>Bot Admin Panel</h2>
    <p>Эндпоинты:</p>
    <ul>
        <li>GET /stats — статистика</li>
        <li>GET /users — все юзеры</li>
        <li>GET /users/{id}/tasks — задачи юзера</li>
        <li>POST /broadcast {"text": "..."} — рассылка</li>
        <li>POST /send/{id} {"text": "..."} — написать юзеру</li>
    </ul>
    </body></html>
    """

def start_admin():
    thread = threading.Thread(
        target=uvicorn.run,
        args=(app,),
        kwargs={"host": "0.0.0.0", "port": 8080},
        daemon=True
    )
    thread.start()