import threading
import uvicorn
from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import aiosqlite, os

app = FastAPI()
security = HTTPBasic()

def check_auth(credentials: HTTPBasicCredentials = Depends(security)):
    if credentials.password != os.getenv("ADMIN_PASSWORD"):
        raise HTTPException(status_code=401)

@app.get("/users")
async def users(auth=Depends(check_auth)):
    async with aiosqlite.connect("/app/data/tasks.db") as db:
        cursor = await db.execute("SELECT telegram_id, first_name FROM users")
        return await cursor.fetchall()
def start_admin():
    thread = threading.Thread(
        target=uvicorn.run,
        args=(app,),
        kwargs={"host": "0.0.0.0", "port": 8080},
        daemon=True
    )
    thread.start()