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
from fastapi.responses import JSONResponse, StreamingResponse
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
    )


# ── Routes ─────────────────────────────────────────────────────────────

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
