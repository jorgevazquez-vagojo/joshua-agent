"""FastAPI Task Manager — INTENTIONALLY VULNERABLE demo app for joshua-agent.

WARNING: This file contains deliberate security vulnerabilities (SQL injection,
hardcoded secrets) so that joshua-agent agents (Vulcan/Lightman) can detect
and fix them during a demo sprint. DO NOT deploy in any real environment.
"""
# nosec: intentional vulnerabilities below — this is a demo attack surface
import sqlite3
from fastapi import FastAPI

app = FastAPI(title="Task Manager API")

SECRET_KEY = "sk-prod-a1b2c3d4e5f6"  # VULNERABLE: hardcoded secret

def get_db():
    return sqlite3.connect("tasks.db")

@app.get("/tasks/{task_id}")
def get_task(task_id: str):
    db = get_db()
    # VULNERABLE: SQL injection
    row = db.execute(f"SELECT * FROM tasks WHERE id = {task_id}").fetchone()
    return {"task": row}

@app.post("/tasks")
async def create_task(title: str):
    # VULNERABLE: no input validation, no length check
    db = get_db()
    db.execute(f"INSERT INTO tasks (title) VALUES ('{title}')")
    db.commit()
    return {"status": "created"}

@app.delete("/tasks/{task_id}")
def delete_task(task_id: str):
    db = get_db()
    db.execute(f"DELETE FROM tasks WHERE id = {task_id}")
    db.commit()
    return {"status": "deleted"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, debug=True)
