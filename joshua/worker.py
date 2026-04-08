"""Sprint worker — runs in an isolated subprocess.

Each sprint executes in its own process for full isolation.
The worker writes heartbeats and cycle updates directly to SQLite.
Graceful stop via SIGTERM → sprint.stop().
"""
from __future__ import annotations

import logging
import os
import signal
import threading
import time
from pathlib import Path

from joshua.utils.redact import redact_secrets

log = logging.getLogger("joshua")


def run_worker(
    sprint_id: str,
    config: dict,
    db_path: str,
    log_dir: str,
    callback_url: str | None = None,
) -> None:
    """Top-level function executed inside a child process (must be picklable)."""
    # Re-initialize logging — child process has no handlers after spawn
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    from joshua.persistence import SprintDB
    from joshua.sprint import Sprint
    from joshua.integrations.hub_callback import setup_hub_integration

    db = SprintDB(db_path)
    pid = os.getpid()
    db.update_pid(sprint_id, pid)

    # Build Sprint inside worker (not passed across process boundary)
    sprint = Sprint(config)
    sprint.setup_sprint_logger(sprint_id, Path(log_dir))

    # Hub integration (if configured)
    setup_hub_integration(sprint, config)

    # Install SIGTERM handler for graceful stop
    def _sigterm_handler(signum, frame):
        sprint.sprint_logger.info("SIGTERM received — requesting graceful stop")
        db.update_worker_state(sprint_id, "stopping")
        sprint.stop()

    signal.signal(signal.SIGTERM, _sigterm_handler)

    # Compose cycle callback: DB persistence + optional external URL
    def _cycle_callback(cycle_data: dict):
        db.update_cycle(
            sprint_id=sprint_id,
            cycle=sprint.cycle,
            stats=sprint.stats,
            verdict=cycle_data.get("verdict", ""),
            severity=sprint.last_gate_severity,
            source=sprint.last_verdict_source,
        )
        if callback_url:
            _post_callback(callback_url, cycle_data)

    sprint.on_cycle_complete = _cycle_callback

    # Start heartbeat thread
    _stop_heartbeat = threading.Event()
    hb_thread = threading.Thread(
        target=_heartbeat_loop,
        args=(db, sprint_id, pid, _stop_heartbeat),
        daemon=True,
        name=f"heartbeat-{sprint_id}",
    )
    hb_thread.start()

    # Run the sprint
    db.update_worker_state(sprint_id, "running")
    sprint.sprint_logger.info(f"Worker process {pid} running sprint {sprint_id}")

    try:
        sprint.run()
        db.complete_sprint(sprint_id, "completed")
        sprint.sprint_logger.info(f"Sprint {sprint_id} completed normally")
    except Exception as e:
        safe_error = redact_secrets(str(e))
        db.complete_sprint(sprint_id, "failed", error=safe_error)
        sprint.sprint_logger.error(f"Sprint {sprint_id} crashed: {safe_error}")
    finally:
        _stop_heartbeat.set()
        db.update_worker_state(sprint_id, "exited")


def _heartbeat_loop(
    db,
    sprint_id: str,
    pid: int,
    stop_event: threading.Event,
    interval: int = 30,
) -> None:
    """Periodically write heartbeat to DB so supervisor can detect zombies."""
    while not stop_event.wait(timeout=interval):
        try:
            db.update_heartbeat(sprint_id, pid)
        except Exception:
            pass  # DB write failure is non-fatal for heartbeat


def _post_callback(callback_url: str, cycle_data: dict) -> None:
    """POST cycle data to external callback URL."""
    try:
        import requests
        requests.post(callback_url, json=cycle_data, timeout=10)
    except Exception as e:
        log.warning(f"Callback to {callback_url} failed: {e}")
