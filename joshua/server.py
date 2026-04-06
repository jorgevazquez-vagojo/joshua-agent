"""Joshua HTTP server — manages sprints via REST API.

Used by Brain and other systems to start/stop/monitor sprints programmatically.
Sprints run in background threads; the server is the control plane.

    joshua serve --port 8100
"""

import logging
import os
import threading
import uuid
from datetime import datetime

from fastapi import FastAPI, HTTPException, Depends, Header
from pydantic import BaseModel

from joshua.sprint import Sprint
from joshua.integrations.brain_callback import setup_brain_integration

log = logging.getLogger("joshua")

app = FastAPI(
    title="Joshua Sprint Server",
    description="Autonomous multi-agent sprint orchestration API",
    version="0.1.0",
)

# ── Auth ──────────────────────────────────────────────────────────────

INTERNAL_TOKEN = os.environ.get("JOSHUA_INTERNAL_TOKEN", "")


def verify_token(x_internal_token: str = Header(default="")):
    """Validate internal service token if configured."""
    if INTERNAL_TOKEN and x_internal_token != INTERNAL_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")


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


# ── Models ────────────────────────────────────────────────────────────

class StartSprintRequest(BaseModel):
    """Sprint config in JSON (same schema as YAML config)."""
    config: dict
    callback_url: str | None = None


class SprintStatus(BaseModel):
    sprint_id: str
    project: str
    cycle: int
    stats: dict
    gate_blocked: bool
    running: bool
    started_at: str
    error: str | None = None


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
            headers = {}
            if INTERNAL_TOKEN:
                headers["X-Internal-Token"] = INTERNAL_TOKEN
            req.post(callback_url, json=cycle_data, headers=headers, timeout=10)
        except Exception as e:
            log.warning(f"Callback to {callback_url} failed: {e}")

    return callback


# ── Routes ────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "sprints": len(_registry)}


@app.post("/sprints", response_model=SprintStatus, dependencies=[Depends(verify_token)])
def start_sprint(req: StartSprintRequest):
    """Start a new sprint from a JSON config."""
    config = req.config

    # Validate minimal config
    if "project" not in config or "agents" not in config:
        raise HTTPException(400, "Config must have 'project' and 'agents' sections")

    # Ensure project path exists
    project_path = config["project"].get("path", "")
    if project_path:
        os.makedirs(project_path, exist_ok=True)

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
    setup_brain_integration(sprint, config)

    # Generic callback (overrides Brain callback if both set)
    if req.callback_url:
        sprint.on_cycle_complete = _make_callback(req.callback_url)

    thread = threading.Thread(
        target=_run_sprint_thread,
        args=(None,),  # placeholder, set below
        daemon=True,
        name=f"sprint-{sprint_id}",
    )

    entry = SprintEntry(sprint_id, sprint, thread, config)
    thread._args = (entry,)  # fix circular ref

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
