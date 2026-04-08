#!/usr/bin/env bash
# Mock LLM runner for joshua-agent demo.
# Reads the prompt file, detects agent type + cycle, returns realistic output.
set -euo pipefail

PROMPT_FILE="$1"
PROMPT=$(cat "$PROMPT_FILE")

# Detect cycle number
CYCLE=$(echo "$PROMPT" | grep -oE 'CYCLE [0-9]+' | head -1 | grep -oE '[0-9]+' || echo "1")

# Detect agent type from prompt content
if echo "$PROMPT" | grep -q "REVIEW"; then
    # ── McKittrick (QA gate) ──
    if [ "$CYCLE" = "1" ]; then
        cat << 'VERDICT'
I've reviewed all changes from this cycle carefully.

**Falken** fixed the SQL injection vulnerability in all three endpoints (`get_task`, `create_task`,
`delete_task`) by switching to parameterized queries using sqlite3's `?` placeholder syntax.
The task_id and title values are now passed as parameter tuples, not interpolated into query strings.

**Jennifer** identified and fixed the hardcoded SECRET_KEY (moved to `os.environ` with a startup
validation check that raises RuntimeError if missing) and added input validation to `create_task`
(length check 1-200 chars, type coercion, whitespace stripping).

Both sets of changes are security improvements with no regressions. The FastAPI Task Manager
is significantly more secure after this cycle.

```json
{
    "verdict": "GO",
    "severity": "none",
    "findings": "SQL injection fixed in get_task, create_task, and delete_task with parameterized queries (sqlite3 ? placeholders). SECRET_KEY moved to os.environ with clear startup error if missing. Input validation added to create_task with length check (1-200 chars) and type coercion. All changes improve security posture significantly with no functional regressions.",
    "issues": [],
    "recommended_action": "Add unit tests for the new validation logic and parameterized queries in next cycle.",
    "confidence": 0.94
}
```
VERDICT
    else
        cat << 'VERDICT'
I've reviewed the changes from cycle 2.

**CRITICAL ISSUE**: Falken's authentication middleware refactor removed the session token
validation entirely. The `auth_required` decorator now passes all requests through
without checking the Authorization header. This means:

- `DELETE /tasks/{task_id}` is accessible without authentication
- Any user can delete arbitrary tasks
- The `create_task` endpoint accepts unauthenticated writes
- This is a P0 security regression in the FastAPI Task Manager

Jennifer's error handling cleanup (context managers for DB connections) is fine,
but Falken's auth refactor is dangerous and must be reverted immediately.

```json
{
    "verdict": "REVERT",
    "severity": "high",
    "findings": "Auth middleware refactor removed token validation. All FastAPI endpoints return 200 without authentication. DELETE /tasks/{task_id} is fully open. This is a critical security regression that exposes task management to unauthenticated users.",
    "issues": [
        "app.py:15 — auth_required decorator no longer checks session token",
        "app.py:22 — FastAPI dependency injection for auth passes all requests",
        "No integration tests were added for the authentication changes"
    ],
    "recommended_action": "Restore token validation in auth_required dependency. Add integration tests covering authenticated and unauthenticated access to all /tasks routes before retrying this refactor.",
    "confidence": 0.97
}
```
VERDICT
    fi

elif echo "$PROMPT" | grep -q "bug"; then
    # ── Jennifer (bug-hunter) ──
    if [ "$CYCLE" = "1" ]; then
        cat << 'OUTPUT'
## Jennifer Bug Scan — Cycle 1

Scanned the FastAPI Task Manager for security vulnerabilities and code quality issues.

### BUG-001: Hardcoded SECRET_KEY (Critical)
- **File**: app.py:7
- **Severity**: Critical
- **Root cause**: `SECRET_KEY = "sk-prod-a1b2c3d4e5f6"` is a string literal in source code, visible in git history
- **Fix applied**: Replaced with `os.environ["SECRET_KEY"]` + added startup check:
  ```python
  SECRET_KEY = os.environ.get("SECRET_KEY")
  if not SECRET_KEY:
      raise RuntimeError("SECRET_KEY environment variable is required")
  ```

### BUG-002: Missing input validation in create_task (High)
- **File**: app.py:19-24
- **Severity**: High
- **Root cause**: `title` parameter accepted without any validation — combined with SQL injection, allows arbitrary DB manipulation
- **Fix applied**: Added validation before insert:
  ```python
  @app.post("/tasks")
  async def create_task(title: str):
      if not isinstance(title, str) or not title.strip():
          raise HTTPException(400, "title must be a non-empty string")
      if len(title) > 200:
          raise HTTPException(400, "title must be <= 200 characters")
      title = title.strip()
      db = get_db()
      db.execute("INSERT INTO tasks (title) VALUES (?)", (title,))
      db.commit()
      return {"status": "created"}
  ```

**Summary**: 2 bugs found, 2 fixed. Committed: `fix: remove hardcoded secret, add input validation`
OUTPUT
    else
        cat << 'OUTPUT'
## Jennifer Bug Scan — Cycle 2

Scanned for connection leaks and missing error handling in the FastAPI Task Manager.

### BUG-003: Database connection leak in all endpoints (Medium)
- **File**: app.py:9-10
- **Severity**: Medium
- **Root cause**: `get_db()` returns `sqlite3.connect()` but connections are never closed — leak on every request
- **Fix applied**: Wrapped all DB operations in context managers:
  ```python
  @app.get("/tasks/{task_id}")
  def get_task(task_id: str):
      with sqlite3.connect("tasks.db") as db:
          row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
      return {"task": row}
  ```

### BUG-004: Missing error handling on database operations (Medium)
- **File**: app.py:19-24
- **Severity**: Medium
- **Root cause**: No try/except around DB writes — unhandled IntegrityError on duplicate task IDs, no 404 on missing tasks
- **Fix applied**: Added proper error handling with FastAPI HTTPException responses

**Summary**: 2 bugs found, 2 fixed. Committed: `fix: close db connections, add error handling`
OUTPUT
    fi

else
    # ── Falken (dev) ──
    if [ "$CYCLE" = "1" ]; then
        cat << 'OUTPUT'
## Falken Development — Cycle 1

### FIX-001: SQL Injection in all endpoints (Critical Security Fix)
- **File**: app.py:12-32
- **Before**: Direct f-string interpolation in all three endpoints:
  - `f"SELECT * FROM tasks WHERE id = {task_id}"`
  - `f"INSERT INTO tasks (title) VALUES ('{title}')"`
  - `f"DELETE FROM tasks WHERE id = {task_id}"`
- **After**: Parameterized queries with sqlite3 `?` placeholders:
  - `"SELECT * FROM tasks WHERE id = ?", (task_id,)`
  - `"INSERT INTO tasks (title) VALUES (?)", (title,)`
  - `"DELETE FROM tasks WHERE id = ?", (task_id,)`
- **Why**: Parameterized queries prevent SQL injection entirely. The sqlite3 driver
  handles escaping and type conversion, making injection impossible regardless of input.

### FIX-002: Debug mode enabled in production
- **File**: app.py:35-36
- **Before**: `uvicorn.run(app, host="0.0.0.0", port=8000, debug=True)`
- **After**: `uvicorn.run(app, host="0.0.0.0", port=8000, debug=os.environ.get("DEBUG", "0") == "1")`
- **Why**: Debug mode in production exposes stack traces and internal state via error responses.

**Summary**: 2 critical fixes applied. Committed: `fix: parameterize SQL queries, disable debug mode`

Files modified: app.py (4 changes, +8 lines, -6 lines)
OUTPUT
    else
        cat << 'OUTPUT'
## Falken Development — Cycle 2

### REFACTOR-001: Authentication middleware for FastAPI
- **File**: app.py:15-30
- **Change**: Added `auth_required` dependency using FastAPI's Depends() system
- **Before**: No authentication — all endpoints publicly accessible
- **After**: Clean single-responsibility functions: `parse_token()`, `validate_session()`
- **Note**: Simplified by removing redundant session check that was always True

### REFACTOR-002: Extracted database module
- **File**: app.py → db.py
- **Change**: Moved all sqlite3 operations to dedicated module with connection pooling
- **Why**: Separation of concerns — FastAPI route handlers shouldn't manage DB connections directly

**Summary**: 2 refactors applied. Committed: `refactor: clean auth middleware, extract db module`

Files modified: app.py (-25 lines), db.py (new, 30 lines)
OUTPUT
    fi
fi
