"""Joshua HTTP server — manages sprints via REST API.

Used by orchestrators to start/stop/monitor sprints programmatically.
Sprints run in background threads; state is persisted in SQLite.

    joshua serve --port 8100
"""

import hmac
import ipaddress
import logging
import os
import socket
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Depends, Header, Query
from pydantic import BaseModel, ValidationError, field_validator

from joshua import __version__
from joshua.sprint import Sprint
from joshua.config_schema import JoshuaConfig
from joshua.integrations.hub_callback import setup_hub_integration
from joshua.persistence import SprintDB

log = logging.getLogger("joshua")

# ── Auth ──────────────────────────────────────────────────────────────

INTERNAL_TOKEN = os.environ.get("JOSHUA_INTERNAL_TOKEN", "")
SPRINT_LOG_DIR = Path(os.environ.get("JOSHUA_LOG_DIR", ".joshua/logs"))

# Singleton DB — initialized in lifespan
_db: SprintDB | None = None


def verify_token(x_internal_token: str = Header(default="")):
    """Validate internal service token — required, no exceptions."""
    if not INTERNAL_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="Server not configured: set JOSHUA_INTERNAL_TOKEN env var before starting."
        )
    if not hmac.compare_digest(x_internal_token, INTERNAL_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid or missing X-Internal-Token")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _db
    if not INTERNAL_TOKEN:
        raise RuntimeError(
            "JOSHUA_INTERNAL_TOKEN is not set. "
            'Generate one with: python3 -c "import secrets; print(secrets.token_hex(32))" '
            "and export it before starting the server."
        )
    _db = SprintDB()
    interrupted = _db.mark_interrupted_on_startup()
    if interrupted:
        log.warning(f"Server restart: marked {interrupted} sprint(s) as 'interrupted'")
    yield
    # Graceful shutdown: stop all running sprints
    with _lock:
        live = [(sid, e) for sid, e in _registry.items() if e.thread.is_alive()]
    if live:
        log.info(f"Shutting down: stopping {len(live)} running sprint(s)...")
        for sid, entry in live:
            entry.sprint.stop()
        for sid, entry in live:
            entry.thread.join(timeout=30)
            if entry.thread.is_alive():
                log.warning(f"Sprint {sid} did not stop within 30s")
            elif _db:
                _db.complete_sprint(sid, "stopped")


app = FastAPI(
    title="Joshua Sprint Server",
    description="Autonomous multi-agent sprint orchestration API",
    version=__version__,
    lifespan=lifespan,
)


# ── Sprint Registry ──────────────────────────────────────────────────

class SprintEntry:
    """Tracks a running sprint and its thread."""

    def __init__(self, sprint_id: str, sprint: Sprint, thread: threading.Thread,
                 config: dict):
        self.sprint_id = sprint_id
        self.sprint = sprint
        self.thread = thread
        self.config = config
        self.started_at = datetime.now().isoformat()
        self.error: str | None = None


_registry: dict[str, SprintEntry] = {}
_lock = threading.Lock()
MAX_CONCURRENT_SPRINTS = int(os.environ.get("JOSHUA_MAX_SPRINTS", "10"))


def _cleanup_registry():
    """Remove finished sprint entries from in-memory registry to prevent leaks."""
    with _lock:
        dead = [sid for sid, e in _registry.items() if not e.thread.is_alive()]
        for sid in dead:
            del _registry[sid]
    if dead:
        log.info(f"Registry cleanup: removed {len(dead)} finished sprint(s)")


# ── Models ────────────────────────────────────────────────────────────

class StartSprintRequest(BaseModel):
    """Sprint config in JSON (same schema as YAML config)."""
    config: dict
    callback_url: str | None = None
    config_version: str = "1"  # reserved for future schema evolution

    @field_validator("callback_url", mode="before")
    @classmethod
    def validate_callback_url(cls, v: str | None) -> str | None:
        if v is None:
            return v
        from urllib.parse import urlparse
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("callback_url must use http or https")
        host = parsed.hostname or ""
        if not host:
            raise ValueError("callback_url must have a hostname")

        # Resolve DNS to actual IP — prevents DNS rebinding attacks
        try:
            resolved = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
            ips = {r[4][0] for r in resolved}
        except socket.gaierror:
            raise ValueError(f"callback_url hostname cannot be resolved: {host}")

        for ip_str in ips:
            ip = ipaddress.ip_address(ip_str)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                raise ValueError(
                    f"callback_url resolves to non-public address: {ip_str}"
                )
        return v


class SprintStatus(BaseModel):
    sprint_id: str
    project: str
    cycle: int
    stats: dict
    gate_blocked: bool
    running: bool
    status: str = "running"   # running | completed | failed | interrupted | stopped
    started_at: str
    error: str | None = None
    last_verdict: str | None = None
    last_verdict_severity: str = "none"
    last_gate_issues_count: int = 0
    last_verdict_source: str = "none"  # "json" | "legacy" | "default" | "none"


class StopResponse(BaseModel):
    sprint_id: str
    stopped: bool


class SprintLogsResponse(BaseModel):
    sprint_id: str
    lines: list[str]
    total_lines: int


# ── Helpers ───────────────────────────────────────────────────────────

def _db_cycle_callback(sprint_id: str, original_callback=None):
    """Returns an on_cycle_complete callback that persists state to DB."""
    def callback(cycle_data: dict):
        if _db:
            with _lock:
                entry = _registry.get(sprint_id)
            if entry:
                _db.update_cycle(
                    sprint_id=sprint_id,
                    cycle=entry.sprint.cycle,
                    stats=entry.sprint.stats,
                    verdict=cycle_data.get("verdict", ""),
                    severity=entry.sprint.last_gate_severity,
                    source=entry.sprint.last_verdict_source,
                )
        if original_callback:
            try:
                original_callback(cycle_data)
            except Exception as e:
                log.warning(f"Cycle callback error for {sprint_id}: {e}")
    return callback


def _run_sprint_thread(entry: SprintEntry):
    """Target function for sprint background thread."""
    try:
        entry.sprint.run()
        if _db:
            _db.complete_sprint(entry.sprint_id, "completed")
    except Exception as e:
        entry.error = str(e)
        log.error(f"Sprint {entry.sprint_id} crashed: {e}")
        if _db:
            _db.complete_sprint(entry.sprint_id, "failed", error=str(e))


def _make_callback(callback_url: str):
    """Create an on_cycle_complete callback that POSTs to a URL."""
    import requests as req

    def callback(cycle_data: dict):
        try:
            req.post(callback_url, json=cycle_data, timeout=10)
        except Exception as e:
            log.warning(f"Callback to {callback_url} failed: {e}")

    return callback


def _status_from_entry(sid: str, entry: SprintEntry) -> SprintStatus:
    return SprintStatus(
        sprint_id=sid,
        project=entry.sprint.project_name,
        cycle=entry.sprint.cycle,
        stats=entry.sprint.stats,
        gate_blocked=entry.sprint.gate_blocked,
        running=entry.thread.is_alive(),
        status="running" if entry.thread.is_alive() else "completed",
        started_at=entry.started_at,
        error=entry.error,
        last_verdict=entry.sprint.last_verdict_source if entry.sprint.last_gate_severity != "none" else None,
        last_verdict_severity=entry.sprint.last_gate_severity,
        last_gate_issues_count=len(entry.sprint.last_gate_issues),
        last_verdict_source=entry.sprint.last_verdict_source,
    )


def _status_from_db(row: dict) -> SprintStatus:
    return SprintStatus(
        sprint_id=row["sprint_id"],
        project=row["project"],
        cycle=row["cycle"],
        stats=row["stats"],
        gate_blocked=False,
        running=False,
        status=row["status"],
        started_at=row["started_at"],
        error=row.get("error"),
        last_verdict=row.get("last_verdict"),
        last_verdict_severity=row.get("last_verdict_severity", "none"),
        last_gate_issues_count=0,
        last_verdict_source=row.get("last_verdict_source", "none"),
    )


# ── Routes ────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    with _lock:
        running = sum(1 for e in _registry.values() if e.thread.is_alive())
        total_live = len(_registry)
    total_db = len(_db.list_sprints()) if _db else total_live
    return {
        "status": "ok",
        "version": __version__,
        "sprints_total": total_db,
        "sprints_running": running,
    }


@app.post("/sprints", response_model=SprintStatus, dependencies=[Depends(verify_token)])
def start_sprint(req: StartSprintRequest):
    """Start a new sprint from a JSON config."""
    config = req.config

    # Validate config version
    if req.config_version != "1":
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported config_version: {req.config_version}. Supported: ['1']"
        )

    # Validate config schema
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

    # Validate project path — required, must exist on disk
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

    # Cleanup finished entries before counting
    _cleanup_registry()

    # Rate limiting: cap concurrent running sprints
    with _lock:
        running_count = sum(1 for e in _registry.values() if e.thread.is_alive())
    if running_count >= MAX_CONCURRENT_SPRINTS:
        raise HTTPException(
            status_code=429,
            detail=f"Too many concurrent sprints ({running_count}/{MAX_CONCURRENT_SPRINTS}). "
                   f"Stop a sprint or raise JOSHUA_MAX_SPRINTS env var."
        )

    sprint_id = str(uuid.uuid4())[:8]
    sprint = Sprint(config)

    # Per-sprint log file
    sprint.setup_sprint_logger(sprint_id, SPRINT_LOG_DIR)

    # Hub integration (if configured)
    setup_hub_integration(sprint, config)

    # Compose cycle callback: DB persistence + optional external URL
    external_cb = _make_callback(req.callback_url) if req.callback_url else None
    sprint.on_cycle_complete = _db_cycle_callback(sprint_id, external_cb)

    # Create entry first, then thread (no _args hack)
    entry = SprintEntry(sprint_id, sprint, None, config)
    thread = threading.Thread(
        target=_run_sprint_thread,
        args=(entry,),
        daemon=True,
        name=f"sprint-{sprint_id}",
    )
    entry.thread = thread

    # Persist before starting thread
    if _db:
        _db.insert_sprint(sprint_id, sprint.project_name, config, entry.started_at)

    with _lock:
        _registry[sprint_id] = entry

    thread.start()
    log.info(f"Sprint {sprint_id} started for {sprint.project_name}")

    return _status_from_entry(sprint_id, entry)


@app.get("/sprints", dependencies=[Depends(verify_token)])
def list_sprints():
    """List all sprints — live from registry + historical from DB."""
    result = []
    with _lock:
        live_ids = set(_registry.keys())
        for sid, entry in _registry.items():
            result.append(_status_from_entry(sid, entry))

    # Append finished sprints from DB not in live registry
    if _db:
        for row in _db.list_sprints():
            if row["sprint_id"] not in live_ids:
                result.append(_status_from_db(row))

    return result


@app.get("/sprints/{sprint_id}", response_model=SprintStatus,
         dependencies=[Depends(verify_token)])
def get_sprint(sprint_id: str):
    """Get status of a specific sprint (live or historical)."""
    with _lock:
        entry = _registry.get(sprint_id)
    if entry:
        return _status_from_entry(sprint_id, entry)

    # Fall back to DB for finished sprints
    if _db:
        row = _db.get_sprint(sprint_id)
        if row:
            return _status_from_db(row)

    raise HTTPException(404, f"Sprint {sprint_id} not found")


@app.post("/sprints/{sprint_id}/stop", response_model=StopResponse,
          dependencies=[Depends(verify_token)])
def stop_sprint(sprint_id: str):
    """Request graceful stop of a sprint."""
    with _lock:
        entry = _registry.get(sprint_id)
    if not entry:
        raise HTTPException(404, f"Sprint {sprint_id} not found")

    entry.sprint.stop()
    if _db:
        _db.complete_sprint(sprint_id, "stopped")
    return StopResponse(sprint_id=sprint_id, stopped=True)


@app.get("/sprints/{sprint_id}/logs", response_model=SprintLogsResponse,
         dependencies=[Depends(verify_token)])
def get_sprint_logs(sprint_id: str, lines: int = Query(default=100, ge=1, le=1000)):
    """Return last N lines of the sprint log file (max 1000)."""
    log_file = SPRINT_LOG_DIR / f"sprint-{sprint_id}.log"
    if not log_file.exists():
        raise HTTPException(404, f"Log file not found for sprint {sprint_id}")

    all_lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
    tail = all_lines[-lines:]
    return SprintLogsResponse(
        sprint_id=sprint_id,
        lines=tail,
        total_lines=len(all_lines),
    )
