"""Joshua HTTP server — manages sprints via REST API.

Sprints run in isolated worker processes; all state lives in SQLite.
The server is stateless — reads from DB, delegates to ProcessManager.

    joshua serve --port 8100
"""

from __future__ import annotations

import asyncio
import csv
import ipaddress
import io
import logging
import os
import secrets
import socket
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Depends, Header, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, PlainTextResponse, HTMLResponse
from pydantic import BaseModel, ValidationError, field_validator

from joshua import __version__
from joshua.config_schema import JoshuaConfig
from joshua.persistence import SprintDB
from joshua.process_manager import ProcessManager
from joshua.supervisor import Supervisor
from joshua.utils.redact import redact_secrets

log = logging.getLogger("joshua")

# ── Config ─────────────────────────────────────────────────────────────

INTERNAL_TOKEN = os.environ.get("JOSHUA_INTERNAL_TOKEN", "")
SPRINT_LOG_DIR = Path(os.environ.get("JOSHUA_LOG_DIR", ".joshua/logs"))
MAX_CONCURRENT_SPRINTS = int(os.environ.get("JOSHUA_MAX_SPRINTS", "10"))
AUTO_RESTART = os.environ.get("JOSHUA_AUTO_RESTART", "0") == "1"

# Singletons — initialized in lifespan
_db: SprintDB | None = None
_pm: ProcessManager | None = None
_supervisor: Supervisor | None = None
_server_start: float = time.time()


# ── Auth ───────────────────────────────────────────────────────────────

def verify_token(x_internal_token: str = Header(default="")):
    """Validate internal service token — required, no exceptions."""
    if not INTERNAL_TOKEN or len(INTERNAL_TOKEN.strip()) < 16:
        raise HTTPException(
            status_code=503,
            detail="Server not configured: JOSHUA_INTERNAL_TOKEN must be at least 16 characters."
        )
    if not x_internal_token or not secrets.compare_digest(x_internal_token, INTERNAL_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid or missing X-Internal-Token")


# ── Lifespan ───────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _db, _pm, _supervisor
    if not INTERNAL_TOKEN:
        raise RuntimeError(
            "JOSHUA_INTERNAL_TOKEN is not set. "
            'Generate one with: python3 -c "import secrets; print(secrets.token_hex(32))" '
            "and export it before starting the server."
        )
    _db = SprintDB()
    _pm = ProcessManager(_db, SPRINT_LOG_DIR, MAX_CONCURRENT_SPRINTS)
    _supervisor = Supervisor(
        _db, _pm,
        check_interval=30,
        heartbeat_timeout=90,
        auto_restart=AUTO_RESTART,
    )
    _supervisor.recover_on_startup()
    _supervisor.start()
    log.info("Joshua server started — process-based runtime")
    yield
    # Graceful shutdown
    log.info("Shutting down...")
    _supervisor.stop()
    _pm.stop_all()
    _pm.join_all(timeout=30)
    for row in _db.get_running_sprints():
        _db.complete_sprint(row["sprint_id"], "stopped")
    log.info("Shutdown complete")


app = FastAPI(
    title="Joshua Sprint Server",
    description="Autonomous multi-agent sprint orchestration API",
    version=__version__,
    lifespan=lifespan,
)

# ── CORS ──────────────────────────────────────────────────────────────

_ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get("JOSHUA_ALLOWED_ORIGINS", "").split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS or [],        # empty = no CORS headers (lockdown by default)
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["X-Internal-Token", "Content-Type"],
)


# ── Security middleware ───────────────────────────────────────────────

_rate_limit_state: dict[str, list[float]] = {}
_RATE_LIMIT_WINDOW = 60  # seconds
_RATE_LIMIT_MAX = int(os.environ.get("JOSHUA_RATE_LIMIT", "30"))  # requests per window

# Named constants for explicit rate limiting (v1.11.0)
_rate_limit: dict[str, list[float]] = _rate_limit_state  # alias
RATE_LIMIT_MAX = 60    # requests (for explicit check_rate_limit)
RATE_LIMIT_WINDOW = 60  # seconds


def check_rate_limit(token_prefix: str) -> bool:
    """Return True if allowed, False if rate limited."""
    now = time.time()
    window = _rate_limit.setdefault(token_prefix, [])
    # purge old entries
    _rate_limit[token_prefix] = [t for t in window if now - t < RATE_LIMIT_WINDOW]
    if len(_rate_limit[token_prefix]) >= RATE_LIMIT_MAX:
        return False
    _rate_limit[token_prefix].append(now)
    return True

# Audit log
_AUDIT_LOG_PATH = Path(os.environ.get("JOSHUA_AUDIT_LOG", ".joshua/audit.jsonl"))


@app.middleware("http")
async def audit_log(request: Request, call_next):
    """Write a JSONL audit entry for every API call."""
    import hashlib
    t0 = time.time()
    response = await call_next(request)
    duration_ms = round((time.time() - t0) * 1000)
    token = request.headers.get("x-internal-token", "")
    token_hash = hashlib.sha256(token.encode()).hexdigest()[:12] if token else "anonymous"
    try:
        _AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "method": request.method,
            "path": str(request.url.path),
            "status": response.status_code,
            "duration_ms": duration_ms,
            "token_hash": token_hash,
        }
        with open(_AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(__import__("json").dumps(entry) + "\n")
    except Exception:
        pass  # never let audit log break the request
    return response


def log_audit(action: str, sprint_id: str = "", role: str = "", ip: str = "", token: str = "") -> None:
    """Write a structured audit entry for security-sensitive actions."""
    try:
        import json as _json
        _AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "action": action,
            "sprint_id": sprint_id,
            "role": role,
            "ip": ip,
            "token_prefix": token[:8] if token else "anonymous",
        }
        with open(_AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(_json.dumps(entry) + "\n")
    except Exception:
        pass  # never let audit log break the request


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Add security headers and enforce rate limiting."""
    # Rate limiting (per-token, simple sliding window)
    token = request.headers.get("x-internal-token", "anonymous")
    now = time.time()
    hits = _rate_limit_state.setdefault(token, [])
    hits[:] = [t for t in hits if now - t < _RATE_LIMIT_WINDOW]
    if len(hits) >= _RATE_LIMIT_MAX and request.url.path != "/health":
        return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})
    hits.append(now)

    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


# ── Models ─────────────────────────────────────────────────────────────

def _validate_callback_url(callback_url: str) -> str:
    """Validate callback_url against resolved DNS/IP targets."""
    parsed = urlparse(callback_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("callback_url must be an absolute http(s) URL")

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        addrinfo = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"callback_url hostname could not be resolved: {parsed.hostname}") from exc

    seen = set()
    for _, _, _, _, sockaddr in addrinfo:
        ip = ipaddress.ip_address(sockaddr[0])
        if ip in seen:
            continue
        seen.add(ip)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise ValueError(
                f"callback_url host resolved to a non-public address: {parsed.hostname}"
            )

    return callback_url


class StartSprintRequest(BaseModel):
    """Sprint config in JSON (same schema as YAML config)."""
    config: dict
    callback_url: str | None = None
    config_version: str = "1"

    @field_validator("callback_url", mode="before")
    @classmethod
    def validate_callback_url(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return _validate_callback_url(v)


class SprintStatus(BaseModel):
    sprint_id: str
    project: str
    cycle: int
    stats: dict
    running: bool
    status: str = "running"
    started_at: str
    pid: int | None = None
    worker_state: str = "init"
    error: str | None = None
    last_verdict: str | None = None
    last_verdict_severity: str = "none"
    last_verdict_source: str = "none"
    state: str = "IDLE"
    effort_score: int = 0


class StopResponse(BaseModel):
    sprint_id: str
    stopped: bool


class SprintLogsResponse(BaseModel):
    sprint_id: str
    lines: list[str]
    total_lines: int


# ── Helpers ────────────────────────────────────────────────────────────

def _status_from_db(row: dict, pm: ProcessManager | None = None) -> SprintStatus:
    """Build SprintStatus from DB row, enriching with live process state."""
    sid = row["sprint_id"]
    is_running = row["status"] == "running"
    if is_running and pm:
        is_running = pm.is_alive(sid)
    return SprintStatus(
        sprint_id=sid,
        project=row["project"],
        cycle=row["cycle"],
        stats=row["stats"],
        running=is_running,
        status=row["status"],
        started_at=row["started_at"],
        pid=row.get("pid"),
        worker_state=row.get("worker_state", "init"),
        error=row.get("error"),
        last_verdict=row.get("last_verdict"),
        last_verdict_severity=row.get("last_verdict_severity", "none"),
        last_verdict_source=row.get("last_verdict_source", "none"),
        state=row.get("state", "IDLE"),
        effort_score=row.get("effort_score", 0),
    )


# ── Routes ─────────────────────────────────────────────────────────────

@app.get("/")
def overview():
    """Server overview — version, uptime, running sprints (no auth required)."""
    running_list = []
    total = 0
    running = 0
    if _db:
        rows = _db.list_sprints()
        total = len(rows)
        for row in rows:
            if row["status"] == "running":
                running += 1
                running_list.append({
                    "sprint_id": row["sprint_id"],
                    "project": row["project"],
                    "cycle": row["cycle"],
                    "started_at": row["started_at"],
                })
    return {
        "name": "joshua-agent",
        "version": __version__,
        "uptime_s": round(time.time() - _server_start, 1),
        "sprints_total": total,
        "sprints_running": running,
        "running": running_list,
    }


@app.get("/health")
def health():
    running = _db.running_count() if _db else 0
    total = len(_db.list_sprints()) if _db else 0
    return {
        "status": "ok",
        "version": __version__,
        "runtime": "process",
        "sprints_total": total,
        "sprints_running": running,
    }


@app.get("/metrics")
def metrics():
    """Prometheus text format metrics — no auth required."""
    running = _db.running_count() if _db else 0
    total = len(_db.list_sprints()) if _db else 0
    # Aggregate stats across all sprints
    go = caution = revert = errors = tokens = 0
    if _db:
        for row in _db.list_sprints():
            s = row.get("stats") or {}
            go += s.get("go", 0)
            caution += s.get("caution", 0)
            revert += s.get("revert", 0)
            errors += s.get("errors", 0)
            tokens += s.get("total_tokens", 0)
    uptime = round(time.time() - _server_start, 1)
    lines = [
        "# HELP joshua_sprints_total Total sprints ever created",
        "# TYPE joshua_sprints_total counter",
        f"joshua_sprints_total {total}",
        "# HELP joshua_sprints_running Currently running sprints",
        "# TYPE joshua_sprints_running gauge",
        f"joshua_sprints_running {running}",
        "# HELP joshua_verdicts_total Verdicts issued by gate agents",
        "# TYPE joshua_verdicts_total counter",
        f'joshua_verdicts_total{{verdict="go"}} {go}',
        f'joshua_verdicts_total{{verdict="caution"}} {caution}',
        f'joshua_verdicts_total{{verdict="revert"}} {revert}',
        "# HELP joshua_errors_total Agent execution errors",
        "# TYPE joshua_errors_total counter",
        f"joshua_errors_total {errors}",
        "# HELP joshua_tokens_total Estimated output tokens consumed",
        "# TYPE joshua_tokens_total counter",
        f"joshua_tokens_total {tokens}",
        "# HELP joshua_uptime_seconds Server uptime in seconds",
        "# TYPE joshua_uptime_seconds gauge",
        f"joshua_uptime_seconds {uptime}",
        "",
    ]
    return PlainTextResponse("\n".join(lines), media_type="text/plain; version=0.0.4")


_UI_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Joshua Dashboard</title>
<style>
  * { box-sizing: border-box; }
  body { font-family: monospace; background: #0d1117; color: #e6edf3; padding: 2rem; margin: 0; }
  h1 { color: #58a6ff; border-bottom: 1px solid #30363d; padding-bottom: .5rem; margin-bottom: 1rem; }
  h2 { color: #e6edf3; margin: 0 0 .75rem 0; font-size: 1rem; }
  .card { background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 1rem 1.25rem; margin: 1rem 0; }
  .go { color: #3fb950; } .caution { color: #d29922; } .revert { color: #f85149; }
  .running { color: #58a6ff; } .stopped { color: #8b949e; }
  table { width: 100%; border-collapse: collapse; font-size: .9em; }
  th { text-align: left; color: #8b949e; padding: .35rem .5rem; border-bottom: 1px solid #30363d; }
  td { padding: .35rem .5rem; border-top: 1px solid #21262d; vertical-align: middle; }
  .badge { display: inline-block; padding: .1rem .45rem; border-radius: 4px; font-size: .82em; font-weight: bold; }
  .badge-GO { background: #0f3722; color: #3fb950; }
  .badge-CAUTION { background: #2d1f00; color: #d29922; }
  .badge-REVERT { background: #300e0e; color: #f85149; }
  .badge-running { background: #0c2d6b; color: #58a6ff; }
  .badge-stopped,.badge-done { background: #21262d; color: #8b949e; }
  .stats-row { display: flex; gap: 2rem; flex-wrap: wrap; }
  .stat { min-width: 80px; }
  .stat-val { font-size: 2rem; font-weight: bold; color: #58a6ff; line-height: 1; }
  .stat-lbl { color: #8b949e; font-size: .82em; margin-top: .2rem; }
  .trend { letter-spacing: .1em; font-size: 1.1em; }
  .refresh-bar { color: #8b949e; font-size: .78em; margin-bottom: .5rem; }
  footer { color: #8b949e; font-size: .8em; margin-top: 2rem; border-top: 1px solid #21262d; padding-top: 1rem; }
  footer a { color: #58a6ff; text-decoration: none; }
  .section-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
  @media (max-width: 900px) { .section-grid { grid-template-columns: 1fr; } }
  code { background: #21262d; padding: .1rem .3rem; border-radius: 3px; font-size: .88em; }
  .empty-msg { color: #8b949e; font-style: italic; font-size: .9em; }
</style>
</head>
<body>
<h1>&#9881; Joshua Dashboard</h1>
<div class="refresh-bar" id="refresh-bar">Loading...</div>
<div id="content"></div>
<footer>
  Auto-refreshes every 15s &bull;
  <a href="/metrics">Prometheus</a> &bull;
  <a href="/health">Health</a> &bull;
  <a href="/docs">API Docs</a>
</footer>
<script>
const VERDICT_ORDER = {GO:0, CAUTION:1, REVERT:2};

function badge(v) {
  const cls = 'badge-' + (v||'stopped').replace(/[^A-Z]/g,'') || 'badge-stopped';
  return '<span class="badge ' + cls + '">' + (v||'—') + '</span>';
}

function trendBar(verdicts) {
  if (!verdicts || !verdicts.length) return '<span class="empty-msg">no data</span>';
  return verdicts.slice(-3).map(v => {
    const m = {GO:'<span class="go">&#9679;</span>', CAUTION:'<span class="caution">&#9679;</span>', REVERT:'<span class="revert">&#9679;</span>'};
    return m[v] || '<span style="color:#8b949e">&#9675;</span>';
  }).join(' ');
}

function fmtTime(ts) {
  if (!ts) return '—';
  return ts.slice(0,19).replace('T',' ');
}

function uptime(s) {
  if (s < 60) return s + 's';
  if (s < 3600) return Math.floor(s/60) + 'm ' + (s%60) + 's';
  return Math.floor(s/3600) + 'h ' + Math.floor((s%3600)/60) + 'm';
}

async function load() {
  try {
    const r = await fetch('/');
    const d = await r.json();

    let html = '<div class="card"><div class="stats-row">';
    html += '<div class="stat"><div class="stat-val">' + d.sprints_running + '</div><div class="stat-lbl">Running</div></div>';
    html += '<div class="stat"><div class="stat-val">' + d.sprints_total + '</div><div class="stat-lbl">Total</div></div>';
    html += '<div class="stat"><div class="stat-val">' + uptime(d.uptime_s) + '</div><div class="stat-lbl">Uptime</div></div>';
    html += '<div class="stat"><div class="stat-val" style="font-size:1.1rem;padding-top:.5rem">' + d.version + '</div><div class="stat-lbl">Version</div></div>';
    html += '</div></div>';

    // Active sprints table
    html += '<div class="card"><h2>Active Sprints</h2>';
    if (d.running && d.running.length > 0) {
      html += '<table><tr><th>ID</th><th>Project</th><th>Status</th><th>Cycle</th><th>Last Verdict</th><th>Trend</th><th>Started</th></tr>';
      for (const s of d.running) {
        const trend = trendBar(s.recent_verdicts);
        html += '<tr>';
        html += '<td><code>' + s.sprint_id + '</code></td>';
        html += '<td>' + s.project + '</td>';
        html += '<td>' + badge('running') + '</td>';
        html += '<td>' + (s.cycle||0) + '</td>';
        html += '<td>' + badge(s.last_verdict||'') + '</td>';
        html += '<td class="trend">' + trend + '</td>';
        html += '<td>' + fmtTime(s.started_at) + '</td>';
        html += '</tr>';
      }
      html += '</table>';
    } else {
      html += '<p class="empty-msg">No sprints currently running.</p>';
    }
    html += '</div>';

    // Recent comparisons section
    html += '<div id="compare-section"></div>';

    document.getElementById('content').innerHTML = html;
    document.getElementById('refresh-bar').textContent = 'Last updated: ' + new Date().toLocaleTimeString();

    // Load compare history if available
    loadCompareHistory();
  } catch(e) {
    document.getElementById('content').innerHTML = '<div class="card" style="color:#f85149">Failed to load: ' + e + '</div>';
    document.getElementById('refresh-bar').textContent = 'Error — retrying in 15s';
  }
}

async function loadCompareHistory() {
  try {
    const r = await fetch('/compare-history');
    if (!r.ok) return;
    const rows = await r.json();
    if (!rows || !rows.length) return;
    let html = '<div class="card"><h2>Recent Comparisons</h2><table>';
    html += '<tr><th>Time</th><th>Environments</th><th>Verdicts</th></tr>';
    for (const row of rows.slice(0,5)) {
      html += '<tr><td>' + fmtTime(row.ts) + '</td><td>' + (row.envs||[]).join(', ') + '</td>';
      const vs = (row.verdicts||[]).map(v => badge(v)).join(' ');
      html += '<td>' + vs + '</td></tr>';
    }
    html += '</table></div>';
    document.getElementById('compare-section').innerHTML = html;
  } catch(_) {}
}

load();
setInterval(load, 15000);
</script>
</body>
</html>"""


@app.get("/ui", include_in_schema=False)
def dashboard_ui():
    """Web UI dashboard — no auth required. Auto-refreshes every 10s."""
    return HTMLResponse(_UI_HTML)


@app.post("/sprints", response_model=SprintStatus, dependencies=[Depends(verify_token)])
def start_sprint(req: StartSprintRequest):
    """Start a new sprint in an isolated worker process."""
    config = req.config

    if req.config_version != "1":
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported config_version: {req.config_version}. Supported: ['1']"
        )

    try:
        JoshuaConfig.model_validate(config)
    except ValidationError as e:
        errors = []
        for err in e.errors():
            loc = " \u2192 ".join(str(x) for x in err["loc"])
            errors.append({"field": loc, "error": err["msg"]})
        raise HTTPException(
            status_code=422,
            detail={"message": "Config validation failed", "errors": errors}
        )

    project_path = config.get("project", {}).get("path", "")
    if not project_path:
        raise HTTPException(
            status_code=422,
            detail={"message": "project.path is required"}
        )
    if not Path(project_path).is_dir():
        raise HTTPException(
            status_code=422,
            detail={"message": f"project.path does not exist: {project_path}"}
        )

    running_count = _pm.running_count() if _pm else 0
    if running_count >= MAX_CONCURRENT_SPRINTS:
        raise HTTPException(
            status_code=429,
            detail=f"Too many concurrent sprints ({running_count}/{MAX_CONCURRENT_SPRINTS}). "
                   f"Stop a sprint or raise JOSHUA_MAX_SPRINTS env var."
        )

    sprint_id = str(uuid.uuid4())[:8]
    project_name = config.get("project", {}).get("name", "unknown")
    started_at = datetime.now().isoformat()

    _db.insert_sprint(sprint_id, project_name, config, started_at)
    pid = _pm.spawn(sprint_id, config, callback_url=req.callback_url)
    _db.update_pid(sprint_id, pid)

    log.info(f"Sprint {sprint_id} started for {project_name} (pid={pid})")
    log_audit("sprint.start", sprint_id=sprint_id, role="api")

    row = _db.get_sprint(sprint_id)
    return _status_from_db(row, _pm)


@app.get("/sprints", dependencies=[Depends(verify_token)])
def list_sprints():
    """List all sprints — all from DB (single source of truth)."""
    return [_status_from_db(row, _pm) for row in _db.list_sprints()]


@app.get("/sprints/{sprint_id}", response_model=SprintStatus,
         dependencies=[Depends(verify_token)])
def get_sprint(sprint_id: str):
    """Get status of a specific sprint."""
    row = _db.get_sprint(sprint_id)
    if not row:
        raise HTTPException(404, f"Sprint {sprint_id} not found")
    return _status_from_db(row, _pm)


@app.post("/sprints/{sprint_id}/stop", response_model=StopResponse,
          dependencies=[Depends(verify_token)])
def stop_sprint(sprint_id: str):
    """Request graceful stop of a sprint via SIGTERM."""
    row = _db.get_sprint(sprint_id)
    if not row:
        raise HTTPException(404, f"Sprint {sprint_id} not found")
    if row["status"] != "running":
        raise HTTPException(409, f"Sprint {sprint_id} is not running (status={row['status']})")

    stopped = _pm.stop(sprint_id)
    if stopped:
        _db.update_worker_state(sprint_id, "stopping")
    log_audit("sprint.stop", sprint_id=sprint_id, role="api")
    return StopResponse(sprint_id=sprint_id, stopped=stopped)


@app.get("/sprints/{sprint_id}/logs", response_model=SprintLogsResponse,
         dependencies=[Depends(verify_token)])
def get_sprint_logs(sprint_id: str, lines: int = Query(default=100, ge=1, le=1000)):
    """Return last N lines of the sprint log file (max 1000)."""
    log_file = SPRINT_LOG_DIR / f"sprint-{sprint_id}.log"
    if not log_file.exists():
        raise HTTPException(404, f"Log file not found for sprint {sprint_id}")

    all_lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
    tail = [redact_secrets(line) for line in all_lines[-lines:]]
    return SprintLogsResponse(
        sprint_id=sprint_id,
        lines=tail,
        total_lines=len(all_lines),
    )


@app.get("/sprints/{sprint_id}/logs/stream", dependencies=[Depends(verify_token)])
async def stream_sprint_logs(sprint_id: str):
    """SSE stream of live sprint log — follows the log file like tail -f."""
    log_file = SPRINT_LOG_DIR / f"sprint-{sprint_id}.log"
    if not log_file.exists():
        raise HTTPException(404, f"Log file not found for sprint {sprint_id}")

    async def event_generator():
        with open(log_file, encoding="utf-8", errors="replace") as f:
            # Seek to end — only stream new lines
            f.seek(0, 2)
            while True:
                row = _db.get_sprint(sprint_id) if _db else None
                sprint_running = row and row["status"] == "running"
                line = f.readline()
                if line:
                    safe = redact_secrets(line.rstrip("\n"))
                    yield f"data: {safe}\n\n"
                else:
                    if not sprint_running:
                        yield "event: done\ndata: sprint finished\n\n"
                        break
                    await asyncio.sleep(0.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/sprints/{sprint_id}/verdicts", dependencies=[Depends(verify_token)])
def get_sprint_verdicts(sprint_id: str):
    """Return structured verdict history from results.tsv."""
    row = _db.get_sprint(sprint_id) if _db else None
    if not row:
        raise HTTPException(404, f"Sprint {sprint_id} not found")

    # Derive state_dir from project path (default: <project_path>/.joshua)
    config = row.get("config") or {}
    project_path = config.get("project", {}).get("path", "")
    state_dir_override = config.get("memory", {}).get("state_dir", "")
    state_dir = Path(state_dir_override) if state_dir_override else Path(project_path) / ".joshua"
    tsv_path = state_dir / "results.tsv"

    if not tsv_path.exists():
        return []

    verdicts = []
    try:
        content = tsv_path.read_text(encoding="utf-8", errors="replace")
        reader = csv.DictReader(io.StringIO(content), delimiter="\t")
        for r in reader:
            verdicts.append({
                "cycle": int(r.get("cycle", 0)),
                "verdict": r.get("verdict", ""),
                "duration_s": float(r.get("duration_s", 0) or 0),
                "agents": r.get("agents", ""),
                "confidence": r.get("confidence", ""),
                "metric_before": r.get("metric_before", ""),
                "metric_after": r.get("metric_after", ""),
                "description": r.get("description", ""),
            })
    except Exception as exc:
        log.warning(f"Failed to parse results.tsv for sprint {sprint_id}: {exc}")
        return []

    return verdicts


@app.get("/sprints/{sprint_id}/report", dependencies=[Depends(verify_token)])
def get_sprint_report(sprint_id: str):
    """Aggregated sprint report: verdict trend, avg duration, cost estimate."""
    row = _db.get_sprint(sprint_id) if _db else None
    if not row:
        raise HTTPException(404, f"Sprint {sprint_id} not found")

    config = row.get("config") or {}
    project_path = config.get("project", {}).get("path", "")
    state_dir_override = config.get("memory", {}).get("state_dir", "")
    state_dir = Path(state_dir_override) if state_dir_override else Path(project_path) / ".joshua"
    tsv_path = state_dir / "results.tsv"

    verdicts_list: list[dict] = []
    if tsv_path.exists():
        try:
            content = tsv_path.read_text(encoding="utf-8", errors="replace")
            reader = csv.DictReader(io.StringIO(content), delimiter="\t")
            for r in reader:
                verdicts_list.append({
                    "cycle": int(r.get("cycle", 0)),
                    "verdict": r.get("verdict", "").upper(),
                    "duration_s": float(r.get("duration_s", 0) or 0),
                    "description": r.get("description", ""),
                })
        except Exception as exc:
            log.warning(f"Failed to parse results.tsv for report {sprint_id}: {exc}")

    total_cycles = len(verdicts_list)
    verdict_counts: dict[str, int] = {"GO": 0, "CAUTION": 0, "REVERT": 0}
    durations: list[float] = []
    worst_cycle: dict | None = None

    for v in verdicts_list:
        verdict = v["verdict"]
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
        if v["duration_s"]:
            durations.append(v["duration_s"])
        if verdict == "REVERT" and (
            worst_cycle is None or v["cycle"] > worst_cycle["cycle"]
        ):
            worst_cycle = v

    avg_duration = round(sum(durations) / len(durations), 1) if durations else 0

    # Trend: "improving" if last 3 cycles are all GO, "degrading" if any REVERT, else "stable"
    recent = [v["verdict"] for v in verdicts_list[-3:]]
    if len(recent) == 3 and all(v == "GO" for v in recent):
        trend = "improving"
    elif any(v == "REVERT" for v in recent):
        trend = "degrading"
    else:
        trend = "stable"

    # Cost estimate — tokens_total from DB stats (Sonnet: $3/MTok output)
    stats = row.get("stats") or {}
    tokens_total = stats.get("total_tokens", 0)
    cost_usd = round(tokens_total / 1_000_000 * 3.0, 4)

    return {
        "sprint_id": sprint_id,
        "project": row["project"],
        "status": row["status"],
        "total_cycles": total_cycles,
        "verdicts": verdict_counts,
        "avg_duration_s": avg_duration,
        "worst_cycle": worst_cycle,
        "trend": trend,
        "tokens_total": tokens_total,
        "cost_estimate_usd": cost_usd,
    }


@app.get("/audit", dependencies=[Depends(verify_token)])
def get_audit_log(lines: int = Query(default=100, ge=1, le=5000)):
    """Return last N lines of the audit log (max 5000)."""
    import json as _json
    if not _AUDIT_LOG_PATH.exists():
        return []
    try:
        all_lines = _AUDIT_LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
        tail = all_lines[-lines:]
        return [_json.loads(line) for line in tail if line.strip()]
    except Exception as exc:
        log.warning(f"Failed to read audit log: {exc}")
        return []


# ── Webhook task injection ─────────────────────────────────────────────

class WebhookTaskRequest(BaseModel):
    sprint_id: str
    task: str


@app.post("/webhook/task", dependencies=[Depends(verify_token)])
async def webhook_task(req: WebhookTaskRequest):
    """Queue a task for a sprint via webhook (for CI/CD integration)."""
    import json as _json
    sprint = _db.get_sprint(req.sprint_id) if _db else None
    if not sprint:
        raise HTTPException(404, "Sprint not found")
    config = sprint.get("config", {})
    project_path = config.get("project", {}).get("path", "")
    if not project_path:
        raise HTTPException(400, "Sprint has no project path")
    tasks_path = Path(project_path).expanduser() / ".joshua" / "webhook_tasks.json"
    tasks_path.parent.mkdir(parents=True, exist_ok=True)
    tasks: list = []
    if tasks_path.exists():
        try:
            tasks = _json.loads(tasks_path.read_text())
        except Exception:
            tasks = []
    tasks.append(req.task)
    tasks_path.write_text(_json.dumps(tasks))
    log.info(f"Webhook task queued for sprint {req.sprint_id}: {req.task[:80]}")
    log_audit("webhook.task", sprint_id=req.sprint_id, role="api")
    return {"queued": True, "queue_length": len(tasks)}


# ── Compare history (for UI) ───────────────────────────────────────────

_COMPARE_HISTORY_PATH = Path(os.environ.get("JOSHUA_COMPARE_HISTORY", ".joshua/compare_history.jsonl"))


@app.get("/compare-history", include_in_schema=False)
def compare_history(limit: int = Query(default=10, ge=1, le=100)):
    """Return recent compare runs for the dashboard UI (no auth required)."""
    import json as _json
    history_path = _COMPARE_HISTORY_PATH
    if not history_path.exists():
        return []
    try:
        lines = history_path.read_text(encoding="utf-8", errors="replace").splitlines()
        rows = []
        for line in reversed(lines[-limit:]):
            line = line.strip()
            if line:
                try:
                    rows.append(_json.loads(line))
                except Exception:
                    pass
        return rows
    except Exception as exc:
        log.warning(f"Failed to read compare history: {exc}")
        return []


# ── RBAC ───────────────────────────────────────────────────────────────

_ROLE_LEVELS = {"viewer": 0, "operator": 1, "admin": 2}

# Parse JOSHUA_TOKENS: JSON map {"token": "role", ...}
# Backward compat: if JOSHUA_AUTH_TOKEN set, treat as admin
_ROLE_MAP: dict[str, str] = {}
_rbac_tokens_raw = os.environ.get("JOSHUA_TOKENS", "")
if _rbac_tokens_raw:
    try:
        _ROLE_MAP = __import__("json").loads(_rbac_tokens_raw)
    except Exception:
        log.warning("JOSHUA_TOKENS is not valid JSON — RBAC disabled")
_legacy_admin = os.environ.get("JOSHUA_AUTH_TOKEN", "")
if _legacy_admin and _legacy_admin not in _ROLE_MAP:
    _ROLE_MAP[_legacy_admin] = "admin"


def get_role(request: Request) -> str | None:
    """Extract role from Bearer token or cookie."""
    auth = request.headers.get("Authorization", "")
    token = ""
    if auth.startswith("Bearer "):
        token = auth[7:].strip()
    if not token:
        token = request.cookies.get("joshua_token", "")
    if not token:
        return None
    return _ROLE_MAP.get(token)


def require_role(min_role: str):
    """FastAPI dependency: require at least min_role."""
    def _dep(request: Request):
        if not _ROLE_MAP:
            return  # RBAC not configured — open access
        role = get_role(request)
        if role is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        if _ROLE_LEVELS.get(role, -1) < _ROLE_LEVELS.get(min_role, 999):
            raise HTTPException(status_code=403, detail=f"Insufficient role: need {min_role}")
    return _dep


# ── Login routes ──────────────────────────────────────────────────────

_LOGIN_HTML = """<!DOCTYPE html><html><head><title>Joshua Login</title>
<style>body{font-family:monospace;background:#0d1117;color:#e6edf3;display:flex;justify-content:center;padding-top:4rem;}
.box{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:2rem;min-width:300px;}
h2{color:#58a6ff;margin:0 0 1.5rem 0;}
input{width:100%;padding:.5rem;background:#0d1117;border:1px solid #30363d;color:#e6edf3;border-radius:4px;font-family:monospace;}
button{margin-top:1rem;width:100%;padding:.6rem;background:#238636;color:#fff;border:none;border-radius:4px;cursor:pointer;font-family:monospace;}
.err{color:#f85149;margin-top:.5rem;}</style></head>
<body><div class="box"><h2>&#9881; Joshua Login</h2>
<form method="POST" action="/login">
<label>Token</label><br><input type="password" name="token" autofocus><br>
<button type="submit">Sign in</button>
</form></div></body></html>"""


@app.get("/login", include_in_schema=False)
def login_form():
    return HTMLResponse(_LOGIN_HTML)


@app.post("/login", include_in_schema=False)
async def login_submit(request: Request):
    from fastapi.responses import RedirectResponse
    form = await request.form()
    token = form.get("token", "")
    role = _ROLE_MAP.get(token) if _ROLE_MAP else "admin"
    ip = request.client.host if request.client else ""
    if role is None:
        log_audit("login.failed", ip=ip, token=token)
        return HTMLResponse(_LOGIN_HTML + '<script>document.write("<p class=err>Invalid token.</p>")</script>')
    log_audit("login.success", role=role, ip=ip, token=token)
    resp = RedirectResponse(url="/ui", status_code=303)
    resp.set_cookie("joshua_token", token, httponly=True, samesite="lax")
    return resp


# ── Approval endpoints ────────────────────────────────────────────────

class ApprovalRequest(BaseModel):
    approved: bool


def _get_sprint_state_dir(sprint_id: str) -> Path | None:
    """Derive state dir from sprint config."""
    if not _db:
        return None
    row = _db.get_sprint(sprint_id)
    if not row:
        return None
    config = row.get("config") or {}
    project_path = config.get("project", {}).get("path", "")
    state_dir_override = config.get("memory", {}).get("state_dir", "")
    if state_dir_override:
        return Path(state_dir_override)
    if project_path:
        return Path(project_path) / ".joshua"
    return None


@app.get("/sprints/{sprint_id}/approval", dependencies=[Depends(require_role("operator"))])
def get_approval(sprint_id: str):
    """Return current approval_pending.json if a REVERT approval is waiting."""
    import json as _json
    state_dir = _get_sprint_state_dir(sprint_id)
    if not state_dir:
        raise HTTPException(404, f"Sprint {sprint_id} not found")
    pending_path = state_dir / "approval_pending.json"
    if not pending_path.exists():
        return {"pending": False}
    try:
        data = _json.loads(pending_path.read_text())
        return {"pending": True, **data}
    except Exception:
        return {"pending": False}


@app.post("/sprints/{sprint_id}/approval", dependencies=[Depends(require_role("operator"))])
def post_approval(sprint_id: str, req: ApprovalRequest):
    """Approve or dismiss a pending REVERT action."""
    import json as _json
    state_dir = _get_sprint_state_dir(sprint_id)
    if not state_dir:
        raise HTTPException(404, f"Sprint {sprint_id} not found")
    pending_path = state_dir / "approval_pending.json"
    if not pending_path.exists():
        raise HTTPException(409, "No approval pending for this sprint")
    approval_path = state_dir / "approval.json"
    decision = {"approved": req.approved, "timestamp": datetime.now().isoformat()}
    approval_path.write_text(_json.dumps(decision, indent=2))
    log.info(f"Approval for sprint {sprint_id}: approved={req.approved}")
    log_audit("sprint.approval", sprint_id=sprint_id, role="operator")
    return {"ok": True, "approved": req.approved}


# ── Fleet dashboard ───────────────────────────────────────────────────

@app.get("/fleet")
def fleet_overview():
    """Fleet overview — reads JOSHUA_FLEET_CONFIG env var pointing to fleet.yaml."""
    import json as _json
    fleet_config_path = os.environ.get("JOSHUA_FLEET_CONFIG", "")
    if not fleet_config_path:
        return []
    fleet_file = Path(fleet_config_path)
    if not fleet_file.exists():
        raise HTTPException(404, f"Fleet config not found: {fleet_config_path}")
    try:
        import yaml as _yaml
        fleet_cfg = _yaml.safe_load(fleet_file.read_text())
        sprint_paths = fleet_cfg.get("sprints", [])
    except ImportError:
        raise HTTPException(500, "PyYAML not available")
    except Exception as e:
        raise HTTPException(500, f"Failed to read fleet config: {e}")

    results = []
    for sprint_path in sprint_paths:
        try:
            sp = Path(sprint_path)
            if not sp.exists():
                results.append({"name": str(sprint_path), "error": "config not found"})
                continue
            import yaml as _yaml
            cfg = _yaml.safe_load(sp.read_text())
            project = cfg.get("project", {})
            project_dir = project.get("path", "")
            project_name = project.get("name", sp.stem)
            state_dir = Path(
                cfg.get("memory", {}).get("state_dir", "")
                or (project_dir and str(Path(project_dir) / ".joshua"))
                or ".joshua"
            )
            cp_path = state_dir / "checkpoint.json"
            verdict = cycle = cost_usd = None
            trend = "unknown"
            if cp_path.exists():
                try:
                    cp = _json.loads(cp_path.read_text())
                    verdict = cp.get("last_verdict", "")
                    cycle = cp.get("cycle", 0)
                    cost_usd = cp.get("cost_usd", cp.get("stats", {}).get("cost_usd", 0.0))
                    stats = cp.get("stats", {})
                    total = stats.get("go", 0) + stats.get("caution", 0) + stats.get("revert", 0)
                    if total > 0:
                        go_pct = stats.get("go", 0) / total
                        if go_pct > 0.8:
                            trend = "improving"
                        elif stats.get("revert", 0) / total > 0.3:
                            trend = "degrading"
                        else:
                            trend = "stable"
                except Exception:
                    pass
            results.append({
                "name": project_name,
                "verdict": verdict,
                "cycle": cycle,
                "cost_usd": cost_usd,
                "trend": trend,
            })
        except Exception as e:
            results.append({"name": str(sprint_path), "error": str(e)})

    return results


# ── Weekly digest endpoint ────────────────────────────────────────────

@app.get("/digest")
def weekly_digest():
    """Generate weekly summary across all sprints."""
    if not _db:
        return {}
    rows = _db.list_sprints()
    total_sprints = len(rows)
    total_cycles = 0
    total_cost = 0.0
    verdict_breakdown: dict[str, int] = {"GO": 0, "CAUTION": 0, "REVERT": 0}
    all_findings: list[str] = []

    for row in rows:
        stats = row.get("stats") or {}
        total_cycles += (
            stats.get("go", 0) + stats.get("caution", 0)
            + stats.get("revert", 0) + stats.get("errors", 0)
        )
        total_cost += stats.get("cost_usd", stats.get("total_tokens", 0) / 1_000_000 * 3.0)
        verdict_breakdown["GO"] += stats.get("go", 0)
        verdict_breakdown["CAUTION"] += stats.get("caution", 0)
        verdict_breakdown["REVERT"] += stats.get("revert", 0)

        # Try to read findings from checkpoint
        config = row.get("config") or {}
        project_path = config.get("project", {}).get("path", "")
        state_dir_override = config.get("memory", {}).get("state_dir", "")
        state_dir = (
            Path(state_dir_override)
            if state_dir_override
            else (Path(project_path) / ".joshua" if project_path else None)
        )
        if state_dir:
            cp = state_dir / "checkpoint.json"
            if cp.exists():
                try:
                    import json as _json
                    data = _json.loads(cp.read_text())
                    findings = data.get("last_gate_findings", "")
                    if findings:
                        all_findings.append(findings[:200])
                except Exception:
                    pass

    # Top recurring patterns in findings
    from collections import Counter as _Counter
    word_counts = _Counter()
    for f in all_findings:
        for word in f.lower().split():
            if len(word) > 5:
                word_counts[word] += 1
    top_findings = [w for w, _ in word_counts.most_common(3)]

    return {
        "period": "last 7 days",
        "total_sprints": total_sprints,
        "total_cycles": total_cycles,
        "total_cost_usd": round(total_cost, 4),
        "verdict_breakdown": verdict_breakdown,
        "top_recurring_findings": top_findings,
    }
