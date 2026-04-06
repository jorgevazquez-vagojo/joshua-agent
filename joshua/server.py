"""Joshua HTTP server — manages sprints via REST API.

Used by Brain and other systems to start/stop/monitor sprints programmatically.
Sprints run in background threads; the server is the control plane.

    joshua serve --port 8100
"""

import logging
import os
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException, Depends, Header
from pathlib import Path
from pydantic import BaseModel, ValidationError, field_validator

from joshua.sprint import Sprint
from joshua.config_schema import JoshuaConfig
from joshua.integrations.hub_callback import setup_hub_integration

log = logging.getLogger("joshua")

# ── Auth ──────────────────────────────────────────────────────────────

INTERNAL_TOKEN = os.environ.get("JOSHUA_INTERNAL_TOKEN", "")


def verify_token(x_internal_token: str = Header(default="")):
    """Validate internal service token — required, no exceptions."""
    if not INTERNAL_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="Server not configured: set JOSHUA_INTERNAL_TOKEN env var before starting."
        )
    if x_internal_token != INTERNAL_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Internal-Token")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not INTERNAL_TOKEN:
        raise RuntimeError(
            "JOSHUA_INTERNAL_TOKEN is not set. "
            'Generate one with: python3 -c "import secrets; print(secrets.token_hex(32))" '
            "and export it before starting the server."
        )
    yield


app = FastAPI(
    title="Joshua Sprint Server",
    description="Autonomous multi-agent sprint orchestration API",
    version="0.2.0",
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
        # Block internal/loopback addresses (SSRF protection)
        blocked = ("localhost", "127.", "169.254.", "10.", "192.168.", "::1", "0.0.0.0")
        if any(host == b or host.startswith(b) for b in blocked):
            raise ValueError(f"callback_url host not allowed: {host}")
        return v


class SprintStatus(BaseModel):
    sprint_id: str
    project: str
    cycle: int
    stats: dict
    gate_blocked: bool
    running: bool
    started_at: str
    error: str | None = None
    last_verdict_severity: str = "none"
    last_gate_issues_count: int = 0


class StopResponse(BaseModel):
    sprint_id: str
    stopped: bool


# ── Helpers ───────────────────────────────────────────────────────────

def _run_sprint_thread(entry: SprintEntry):
    """Target function for sprint background thread."""
    try:
        entry.sprint.run()
    except Exception as e:
        entry.error = str(e)
        log.error(f"Sprint {entry.sprint_id} crashed: {e}")


def _make_callback(callback_url: str):
    """Create an on_cycle_complete callback that POSTs to a URL."""
    import requests as req

    def callback(cycle_data: dict):
        try:
            req.post(callback_url, json=cycle_data, timeout=10)
        except Exception as e:
            log.warning(f"Callback to {callback_url} failed: {e}")

    return callback


# ── Routes ────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    running = sum(1 for e in _registry.values() if e.thread.is_alive())
    return {
        "status": "ok",
        "version": "0.2.0",
        "sprints_total": len(_registry),
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

    # Rate limiting: cap concurrent running sprints
    with _lock:
        running_count = sum(1 for e in _registry.values() if e.thread.is_alive())
    if running_count >= MAX_CONCURRENT_SPRINTS:
        raise HTTPException(
            status_code=429,
            detail=f"Too many concurrent sprints ({running_count}/{MAX_CONCURRENT_SPRINTS}). "                   f"Stop a sprint or raise JOSHUA_MAX_SPRINTS env var."
        )

    sprint_id = str(uuid.uuid4())[:8]

    # Setup logging for this sprint
    sprint_log = logging.getLogger("joshua")
    if not sprint_log.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        sprint_log.addHandler(handler)
        sprint_log.setLevel(logging.INFO)

    sprint = Sprint(config)

    # Brain integration (if configured in the config)
    setup_hub_integration(sprint, config)

    # Generic callback (overrides Brain callback if both set)
    if req.callback_url:
        sprint.on_cycle_complete = _make_callback(req.callback_url)

    # Create entry first so thread receives it directly — no _args mutation hack
    entry = SprintEntry(sprint_id, sprint, None, config)
    thread = threading.Thread(
        target=_run_sprint_thread,
        args=(entry,),
        daemon=True,
        name=f"sprint-{sprint_id}",
    )
    entry.thread = thread

    with _lock:
        _registry[sprint_id] = entry

    thread.start()
    log.info(f"Sprint {sprint_id} started for {sprint.project_name}")

    return SprintStatus(
        sprint_id=sprint_id,
        project=sprint.project_name,
        cycle=sprint.cycle,
        stats=sprint.stats,
        gate_blocked=sprint.gate_blocked,
        running=True,
        started_at=entry.started_at,
        last_verdict_severity=sprint.last_gate_severity,
        last_gate_issues_count=len(sprint.last_gate_issues),
    )


@app.get("/sprints", dependencies=[Depends(verify_token)])
def list_sprints():
    """List all tracked sprints."""
    result = []
    with _lock:
        for sid, entry in _registry.items():
            result.append(SprintStatus(
                sprint_id=sid,
                project=entry.sprint.project_name,
                cycle=entry.sprint.cycle,
                stats=entry.sprint.stats,
                gate_blocked=entry.sprint.gate_blocked,
                running=entry.thread.is_alive(),
                started_at=entry.started_at,
                error=entry.error,
                last_verdict_severity=entry.sprint.last_gate_severity,
                last_gate_issues_count=len(entry.sprint.last_gate_issues),
            ))
    return result


@app.get("/sprints/{sprint_id}", response_model=SprintStatus,
         dependencies=[Depends(verify_token)])
def get_sprint(sprint_id: str):
    """Get status of a specific sprint."""
    with _lock:
        entry = _registry.get(sprint_id)
    if not entry:
        raise HTTPException(404, f"Sprint {sprint_id} not found")

    return SprintStatus(
        sprint_id=sprint_id,
        project=entry.sprint.project_name,
        cycle=entry.sprint.cycle,
        stats=entry.sprint.stats,
        gate_blocked=entry.sprint.gate_blocked,
        running=entry.thread.is_alive(),
        started_at=entry.started_at,
        error=entry.error,
        last_verdict_severity=entry.sprint.last_gate_severity,
        last_gate_issues_count=len(entry.sprint.last_gate_issues),
    )


@app.post("/sprints/{sprint_id}/stop", response_model=StopResponse,
           dependencies=[Depends(verify_token)])
def stop_sprint(sprint_id: str):
    """Request graceful stop of a sprint."""
    with _lock:
        entry = _registry.get(sprint_id)
    if not entry:
        raise HTTPException(404, f"Sprint {sprint_id} not found")

    entry.sprint.stop()
    return StopResponse(sprint_id=sprint_id, stopped=True)
