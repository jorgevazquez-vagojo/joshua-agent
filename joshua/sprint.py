"""Main sprint loop — the heart of joshua.

Orchestrates work skills → gate skills in continuous cycles.
Each cycle: pick tasks, run work agents, gate agents review, deploy or revert.
"""

import json
import logging
import os
import re
import threading
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from joshua.agents import Agent, agents_from_config
from joshua.config import load_config
from joshua.runners import runner_factory
from joshua.runners.base import LLMRunner, RunResult
from joshua.memory.lessons import extract_lessons, build_memory_prompt
from joshua.memory.wiki import build_wiki_context, save_raw
from joshua.integrations.git import GitOps
from joshua.integrations.notifications import notifier_factory
from joshua.integrations.trackers import tracker_factory
from joshua.utils.health import check_health
from joshua.utils.redact import redact_secrets
from joshua.utils.safe_cmd import run_command
from joshua.utils.preflight import run_preflight, wait_for_memory
from joshua.gate_contract import GateVerdict

log = logging.getLogger("joshua")


class Sprint:
    """Autonomous multi-agent development sprint."""

    def __init__(self, config: dict):
        self.config = config
        self.project = config["project"]
        self.project_dir = self.project["path"]
        self.project_name = self.project["name"]
        self.health_url = self.project.get("health_url", "")
        self.site_url = self.project.get("site_url", "")

        self.runner: LLMRunner = runner_factory(config)
        self.agents = agents_from_config(config)
        self.git = GitOps(self.project_dir)
        self.notifier = notifier_factory(config)
        self.tracker = tracker_factory(config)

        # Sprint settings
        sprint_conf = config.get("sprint", {})
        self.cycle_sleep = sprint_conf.get("cycle_sleep", 300)
        self.revert_sleep = sprint_conf.get("revert_sleep", self.cycle_sleep)
        self.digest_every = sprint_conf.get("digest_every", 0)
        self.max_cycles = sprint_conf.get("max_cycles", 0)  # 0 = infinite
        self.max_hours = sprint_conf.get("max_hours", 0)  # 0 = infinite
        self.dry_run = sprint_conf.get("dry_run", False)
        self.deploy_cmd = sprint_conf.get("deploy_command", "") or self.project.get("deploy", "")
        self.revert_cmd = sprint_conf.get("revert_command", "")
        self.health_check_command = sprint_conf.get("health_check_command", "")
        self.verdict_policy = sprint_conf.get(
            "verdict_policy",
            {
                "GO": "deploy",
                "CAUTION": "deploy_with_warning",
                "REVERT": "revert",
            },
        )

        # Production features
        self.gate_blocking = sprint_conf.get("gate_blocking", False)
        self.cross_agent_context = sprint_conf.get("cross_agent_context", False)
        self.health_check_enabled = (
            sprint_conf.get("health_check", False)
            or bool(self.health_url)
            or bool(self.health_check_command)
        )
        self.health_check_max_failures = sprint_conf.get("health_check_max_failures", 3)
        self.recovery_deploy = sprint_conf.get("recovery_deploy", "")
        self.retries = sprint_conf.get("retries", 0)
        self.max_consecutive_errors = sprint_conf.get("max_consecutive_errors", 0)
        self.max_backoff = sprint_conf.get("max_backoff", 900)
        self.no_deploy = sprint_conf.get("no_deploy", False)
        self._consecutive_health_failures = 0
        self.git_strategy = sprint_conf.get("git_strategy", "none")
        self.agent_stagger = sprint_conf.get("agent_stagger", 0)  # seconds between agents
        self.min_memory_gb = sprint_conf.get("min_memory_gb", 0)  # wait for RAM before agent

        safety_conf = config.get("safety", {})
        self.allowed_commands = safety_conf.get("allowed_commands", [])
        self.allowed_paths = safety_conf.get("allowed_paths", [])
        self.approval_command = safety_conf.get("approval_command", "")
        self.approval_required_actions = set(
            safety_conf.get("approval_required_actions", [])
        )

        # Memory settings
        mem_conf = config.get("memory", {})
        self.memory_enabled = mem_conf.get("enabled", True)
        self.state_dir = Path(
            mem_conf.get("state_dir", os.path.join(self.project_dir, ".joshua"))
        )
        self.state_dir.mkdir(parents=True, exist_ok=True)

        # Hooks (set by server or external integrations)
        self._stop_requested = False
        self._stop_event = threading.Event()
        self._active_command_process = None
        self._command_lock = threading.Lock()
        self.on_cycle_complete = None  # callable(cycle_data: dict) -> None
        self.context_provider = None   # callable(cycle: int) -> str

        # State
        self.cycle = self._load_checkpoint()
        self.stats = {"go": 0, "caution": 0, "revert": 0, "errors": 0}
        self.cycle_summaries: list[dict] = []
        self.gate_blocked = False
        self.last_gate_findings = ""
        self.last_gate_issues: list = []
        self.last_gate_severity: str = "none"
        self.last_gate_recommended_action: str = ""
        self.last_gate_confidence: float | None = None
        self.last_verdict_source: str = "none"  # "json" | "legacy" | "default"
        self.consecutive_errors = 0
        self._last_agent_attempts = 1
        self._current_cycle_started_at: float | None = None
        self._current_cycle_agent_results: dict[str, dict] = {}
        self._current_cycle_action: str = "skip"
        self._current_cycle_approval: dict[str, str] = {"status": "not_required"}
        self._current_cycle_health: dict[str, str] = {"status": "not_checked"}

        # Per-sprint logger — replaced by setup_sprint_logger() when run via server
        self.sprint_id: str = ""
        self.sprint_logger = log

    def setup_sprint_logger(self, sprint_id: str, log_dir: Path) -> None:
        """Attach a per-sprint rotating file handler.

        Called by the server after sprint construction. The sprint logger is a
        child of the root 'joshua' logger (propagate=True) so messages appear
        in both the sprint file and the main server log.
        """
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"sprint-{sprint_id}.log"
        handler = RotatingFileHandler(
            str(log_file), maxBytes=10 * 1024 * 1024, backupCount=3
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        sprint_logger = logging.getLogger(f"joshua.sprint.{sprint_id}")
        sprint_logger.addHandler(handler)
        sprint_logger.setLevel(logging.INFO)
        sprint_logger.propagate = True
        self.sprint_id = sprint_id
        self.sprint_logger = sprint_logger
        self.sprint_logger.info(f"Sprint {sprint_id} logger initialized → {log_file}")

    def stop(self):
        """Request graceful stop after current cycle completes."""
        self._stop_requested = True
        self._stop_event.set()
        self.runner.cancel()
        self._terminate_active_command()
        self.sprint_logger.info("Stop requested — will finish after current cycle")

    def _wait_or_stop(self, seconds: float) -> bool:
        """Wait up to N seconds, returning True if a stop was requested."""
        if seconds <= 0:
            return self._stop_requested
        return self._stop_event.wait(seconds)

    def _run_dry_run(self) -> str:
        """Emit a structured execution plan without running agents or commands."""
        plan = {
            "project": self.project_name,
            "project_dir": self.project_dir,
            "cycle": self.cycle + 1,
            "runner": self.runner.name,
            "agents": [
                {
                    "name": agent.name,
                    "skill": agent.skill,
                    "phase": agent.phase,
                    "run_when_blocked": agent.run_when_blocked,
                }
                for agent in self.agents
            ],
            "deploy_command": self.deploy_cmd,
            "health_check_command": self.health_check_command,
            "verdict_policy": self.verdict_policy,
            "allowed_commands": self.allowed_commands,
            "allowed_paths": self.allowed_paths,
            "approval_command": self.approval_command,
            "approval_required_actions": sorted(self.approval_required_actions),
        }
        self.sprint_logger.info("DRY-RUN — no agents, deploys, or approvals will be executed")
        self.sprint_logger.info(
            f"Plan: runner={plan['runner']} | agents={len(plan['agents'])} | "
            f"deploy={bool(self.deploy_cmd)} | health_check={bool(self.health_check_command)}"
        )
        self.sprint_logger.info(
            f"Policy: {self.verdict_policy} | approvals={plan['approval_required_actions']}"
        )

        events_dir = self.state_dir / "events"
        events_dir.mkdir(parents=True, exist_ok=True)
        (events_dir / "dry_run.json").write_text(json.dumps(plan, indent=2))
        return "DRY_RUN"

    def _set_active_command(self, process):
        with self._command_lock:
            self._active_command_process = process

    def _clear_active_command(self, process):
        with self._command_lock:
            if self._active_command_process is process:
                self._active_command_process = None

    def _terminate_active_command(self):
        with self._command_lock:
            process = self._active_command_process
        if process is None or process.poll() is not None:
            return
        try:
            if os.name != "nt":
                import signal
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
        except ProcessLookupError:
            return
        except OSError:
            process.terminate()

    def _resolve_verdict_action(self, verdict: str) -> str:
        """Return the configured action for a gate verdict."""
        action = self.verdict_policy.get(verdict.upper())
        valid_actions = {"deploy", "deploy_with_warning", "revert", "skip", "stop"}
        if action not in valid_actions:
            log.warning(
                f"Invalid verdict policy action for {verdict!r}: {action!r} — defaulting to skip"
            )
            return "skip"
        return action

    def _approval_needed(self, action: str) -> bool:
        """Return True when a sensitive action requires human approval."""
        return bool(self.approval_command) and action in self.approval_required_actions

    def _request_approval(self, action: str, detail: str = "") -> bool:
        """Run the configured approval command and return True on approval."""
        if not self._approval_needed(action):
            self._current_cycle_approval = {"status": "not_required"}
            return True

        approval_env = {
            "JOSHUA_PROJECT": self.project_name,
            "JOSHUA_PROJECT_DIR": self.project_dir,
            "JOSHUA_CYCLE": str(self.cycle),
            "JOSHUA_ACTION": action,
            "JOSHUA_ACTION_DETAIL": detail,
        }
        self.sprint_logger.info(f"Approval required for {action} — running approval command")
        success, output = run_command(
            self.approval_command,
            cwd=self.project_dir,
            timeout=300,
            dry_run=self.dry_run,
            extra_env=approval_env,
            cancel_event=self._stop_event,
            on_process_start=self._set_active_command,
            on_process_end=self._clear_active_command,
            allowed_commands=self.allowed_commands,
            allowed_paths=self.allowed_paths,
        )
        self._current_cycle_approval = {
            "status": "approved" if success else "denied",
            "command": self.approval_command,
            "detail": detail,
            "output": output[:500],
        }
        if not success:
            self.sprint_logger.warning(f"Approval denied for {action}: {output[:200]}")
        return success

    def _check_health(self) -> bool:
        """Run the configured health check, preferring command checks over URLs."""
        if self.health_check_command:
            if self.dry_run:
                self._current_cycle_health = {
                    "status": "planned",
                    "mode": "command",
                    "command": self.health_check_command,
                }
                return True
            success, output = run_command(
                self.health_check_command,
                cwd=self.project_dir,
                timeout=300,
                dry_run=False,
                cancel_event=self._stop_event,
                on_process_start=self._set_active_command,
                on_process_end=self._clear_active_command,
                allowed_commands=self.allowed_commands,
                allowed_paths=self.allowed_paths,
            )
            self._current_cycle_health = {
                "status": "healthy" if success else "unhealthy",
                "mode": "command",
                "command": self.health_check_command,
                "output": output[:500],
            }
            return success

        if self.health_url:
            if self.dry_run:
                self._current_cycle_health = {
                    "status": "planned",
                    "mode": "url",
                    "url": self.health_url,
                }
                return True
            success = check_health(self.health_url)
            self._current_cycle_health = {
                "status": "healthy" if success else "unhealthy",
                "mode": "url",
                "url": self.health_url,
            }
            return success

        self._current_cycle_health = {"status": "not_configured"}
        return True

    def _verify_post_revert_health(self) -> bool:
        """Re-check health after a revert action."""
        if not self.health_check_enabled:
            return True

        for attempt in range(1, self.health_check_max_failures + 1):
            if self._check_health():
                self.sprint_logger.info("Post-revert health check passed")
                return True
            self.sprint_logger.warning(
                f"Post-revert health check failed ({attempt}/{self.health_check_max_failures})"
            )
            if self._wait_or_stop(5):
                break

        self.sprint_logger.error("Post-revert health check did not recover")
        return False

    def run(self):
        """Run the sprint loop until stopped, max_cycles, or max_hours reached."""
        if self.dry_run:
            self._run_dry_run()
            return

        # Acquire exclusive lock to prevent concurrent sprints on the same .joshua dir
        lock_path = self.state_dir / "sprint.lock"
        lock_fd = None
        try:
            lock_fd = open(lock_path, "w")
            import fcntl
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (ImportError, OSError):
            # fcntl unavailable (Windows) or lock already held
            if lock_fd:
                lock_fd.close()
                lock_fd = None
            if lock_path.exists():
                try:
                    age = time.time() - lock_path.stat().st_mtime
                    if age < 3600:  # lock younger than 1h → another sprint is running
                        raise RuntimeError(
                            f"Another sprint is already running on {self.state_dir}. "
                            "Stop it first, or delete .joshua/sprint.lock if it's stale."
                        )
                except OSError:
                    pass
            lock_fd = open(lock_path, "w")
        lock_path.write_text(str(os.getpid()))

        self.sprint_logger.info(f"Sprint started for {self.project_name} at {self.project_dir}")
        self.sprint_logger.info(f"Runner: {self.runner.name} | Agents: {[a.name for a in self.agents]}")
        self.sprint_logger.info(f"Cycle sleep: {self.cycle_sleep}s | Memory: {self.memory_enabled}")

        self.notifier.notify_event("start",
            f"Sprint started — {len(self.agents)} agents, runner={self.runner.name}",
            self.project_name)

        start_time = time.monotonic()

        try:
            while not self._stop_requested:
                self.cycle += 1

                if self.max_cycles and self.cycle > self.max_cycles:
                    self.sprint_logger.info(f"Reached max_cycles ({self.max_cycles}). Stopping.")
                    break

                if self.max_hours:
                    elapsed_h = (time.monotonic() - start_time) / 3600
                    if elapsed_h >= self.max_hours:
                        self.sprint_logger.info(f"Reached max_hours ({self.max_hours}h). Stopping.")
                        break

                # Pre-flight checks
                warnings = run_preflight(self.config)
                for w in warnings:
                    self.sprint_logger.warning(f"Preflight: {w}")

                try:
                    verdict = self._run_cycle()
                    self.consecutive_errors = 0
                except Exception as e:
                    self.consecutive_errors += 1
                    self.stats["errors"] += 1
                    self.sprint_logger.error(f"Cycle {self.cycle} error: {e}")
                    self.notifier.notify_event("crash",
                        f"Cycle {self.cycle} error: {str(e)[:200]}",
                        self.project_name)

                    if (self.max_consecutive_errors
                            and self.consecutive_errors >= self.max_consecutive_errors):
                        log.critical(
                            f"{self.consecutive_errors} consecutive errors. Stopping.")
                        break

                    # Exponential backoff
                    backoff = min(self.cycle_sleep * (2 ** self.consecutive_errors), self.max_backoff)
                    self.sprint_logger.info(f"Backing off {backoff}s...")
                    if self._wait_or_stop(backoff):
                        break
                    continue

                self._save_checkpoint()

                # Cycle complete callback (used by server/Brain integration)
                if self.on_cycle_complete:
                    try:
                        self.on_cycle_complete({
                            "cycle": self.cycle,
                            "verdict": verdict,
                            "stats": dict(self.stats),
                            "project": self.project_name,
                            "timestamp": datetime.now().isoformat(),
                        })
                    except Exception as e:
                        self.sprint_logger.warning(f"Cycle callback error: {e}")

                # Digest report
                if self.digest_every and self.cycle % self.digest_every == 0:
                    self._send_digest()

                # Sleep (longer after REVERT)
                sleep_time = self.revert_sleep if verdict == "REVERT" else self.cycle_sleep
                self.sprint_logger.info(f"Sleeping {sleep_time}s before next cycle...")
                if self._wait_or_stop(sleep_time):
                    break

        except KeyboardInterrupt:
            self.sprint_logger.info("Sprint interrupted by user")
        finally:
            self.runner.cancel()
            self._terminate_active_command()
            self._save_checkpoint()
            if lock_fd is not None:
                try:
                    lock_fd.close()
                except OSError:
                    pass
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                pass
            self.sprint_logger.info(f"Sprint ended at cycle {self.cycle}. Stats: {self.stats}")
            self.notifier.notify_event("stop",
                f"Ended at cycle {self.cycle}. Stats: {self.stats}",
                self.project_name)
            # Release lock
            if lock_fd:
                lock_fd.close()
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _run_cycle(self) -> str:
        """Execute one full cycle. Returns verdict string."""
        log.info(f"{'='*60}")
        self.sprint_logger.info(f"CYCLE {self.cycle} — {datetime.now().isoformat(timespec='seconds')}")
        log.info(f"{'='*60}")
        self._current_cycle_started_at = time.monotonic()
        self._current_cycle_agent_results = {}
        self._current_cycle_action = "skip"
        self._current_cycle_approval = {"status": "not_required"}
        self._current_cycle_health = {"status": "not_checked"}

        # Health check — only stop sprint after N consecutive failures
        if self.health_check_enabled:
            if not self._check_health():
                self.sprint_logger.warning("Health check failed — attempting recovery")
                if self.recovery_deploy:
                    self._deploy(self.recovery_deploy, action="recovery_deploy")
                    if self._wait_or_stop(10):
                        return "CAUTION"
                if not self._check_health():
                    self._consecutive_health_failures += 1
                    log.error(
                        f"Still unhealthy after recovery "
                        f"({self._consecutive_health_failures}/{self.health_check_max_failures})"
                        " — skipping cycle"
                    )
                    self.notifier.notify_event("health_fail",
                        f"Cycle {self.cycle} skipped — service unhealthy",
                        self.project_name)
                    if self._consecutive_health_failures >= self.health_check_max_failures:
                        log.error("Max consecutive health failures reached — stopping sprint")
                        self._stop_requested = True
                        self._stop_event.set()
                    return "CAUTION"
            else:
                self._consecutive_health_failures = 0

        # Git snapshot (if enabled)
        branch = None
        if self.git_strategy == "snapshot" and self.git.is_repo():
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            branch = self.git.snapshot(f"sprint/{self.cycle}-{ts}")

        # Categorize agents by phase
        work_agents = [a for a in self.agents if a.phase == "work"]
        gate_agents = [a for a in self.agents if a.phase == "gate"]

        # Gate blocking: only run unblocked agents
        if self.gate_blocked and self.gate_blocking:
            blocked = [a for a in work_agents if not a.run_when_blocked]
            work_agents = [a for a in work_agents if a.run_when_blocked]
            if blocked:
                log.warning(
                    f"Gate BLOCKED — skipping: {[a.name for a in blocked]}")

        context = self._build_context()

        # Phase 1: Run all work skills
        work_outputs = {}
        agent_timings: dict[str, dict] = {}
        for i, agent in enumerate(work_agents):
            # Stagger: wait between agents (skip before first)
            if i > 0:
                self._stagger_wait(agent.name)
            task = agent.get_task(self.cycle)
            self.sprint_logger.info(f"[{agent.name}] ({agent.skill}) Task: {task[:80]}")
            result = self._run_agent_with_retry(agent, task, context)
            output = result.output if result.success else f"[FAILED] {result.error}"
            work_outputs[agent.name] = output
            self._record_result(agent, task, result)
            agent_timings[agent.name] = {
                "phase": agent.phase,
                "skill": agent.skill,
                "duration_seconds": result.duration_seconds,
                "exit_code": result.exit_code,
                "success": result.success,
                "attempts": self._last_agent_attempts,
                "output_chars": len(result.output),
            }
            self._current_cycle_agent_results[agent.name] = {
                "phase": agent.phase,
                "skill": agent.skill,
                "task": task[:500],
                "success": result.success,
                "duration_seconds": result.duration_seconds,
                "attempts": self._last_agent_attempts,
            }

        # Phase 2: Gate skills review all work outputs
        verdict = "GO" if not gate_agents else "CAUTION"
        for i, agent in enumerate(gate_agents):
            if i > 0 or work_outputs:
                self._stagger_wait(agent.name)
            report_parts = []
            for agent_name, output in work_outputs.items():
                report_parts.append(
                    f"=== {agent_name.upper()} REPORT ===\n{output[:6000]}")
            gate_task = "\n\n".join(report_parts)

            self.sprint_logger.info(f"[{agent.name}] ({agent.skill}) Reviewing cycle {self.cycle}...")
            result = self._run_agent_with_retry(agent, gate_task, context)
            verdict = self._parse_verdict(result.output)
            self._record_result(agent, f"gate-cycle-{self.cycle}", result)
            agent_timings[agent.name] = {
                "phase": agent.phase,
                "skill": agent.skill,
                "duration_seconds": result.duration_seconds,
                "exit_code": result.exit_code,
                "success": result.success,
                "attempts": self._last_agent_attempts,
                "output_chars": len(result.output),
            }
            self._current_cycle_agent_results[agent.name] = {
                "phase": agent.phase,
                "skill": agent.skill,
                "task": f"gate-cycle-{self.cycle}",
                "success": result.success,
                "duration_seconds": result.duration_seconds,
                "attempts": self._last_agent_attempts,
            }

            # Store gate findings for cross-agent context
            if self.cross_agent_context:
                self.last_gate_findings = result.output[:2000]

        # Apply verdict
        self.stats[verdict.lower()] = self.stats.get(verdict.lower(), 0) + 1
        self.sprint_logger.info(f"VERDICT: {verdict}")
        action = self._resolve_verdict_action(verdict)
        self._current_cycle_action = action

        if action == "stop":
            self.sprint_logger.warning("Policy requested stop — sprint will end after this cycle")
            self._stop_requested = True
            self._stop_event.set()
        elif action == "revert":
            self.sprint_logger.warning("REVERT — changes will not be deployed")
            if self.gate_blocking:
                self.gate_blocked = True
            if not self._approval_needed("revert") or self._request_approval("revert", branch or self.project_name):
                if self.revert_cmd:
                    self._deploy(self.revert_cmd, action="revert", check_approval=False)
                elif branch and self.git_strategy == "snapshot":
                    self.git.revert(branch)
                self._verify_post_revert_health()
                self.notifier.notify_event("revert",
                    f"Cycle {self.cycle} REVERTED", self.project_name)
            else:
                self.notifier.notify_event(
                    "revert_blocked",
                    f"Cycle {self.cycle} revert blocked by approval",
                    self.project_name,
                )
        else:
            self.gate_blocked = False
            if branch and self.git_strategy == "snapshot" and self.git.is_repo():
                self.git.merge_to_main(branch)
            if not self.no_deploy and self.deploy_cmd and action in ("deploy", "deploy_with_warning"):
                if action == "deploy_with_warning":
                    self.sprint_logger.warning("CAUTION — deploying but flagging for review")
                self._deploy(action=action)

        # Summary
        self.cycle_summaries.append({
            "cycle": self.cycle,
            "verdict": verdict,
            "action": action,
            "timestamp": datetime.now().isoformat(),
        })

        # Agent timings
        self.sprint_logger.info(f"CYCLE {self.cycle} COMPLETE — verdict={verdict}")
        elapsed_seconds = (
            time.monotonic() - self._current_cycle_started_at
            if self._current_cycle_started_at is not None
            else 0.0
        )
        self._write_cycle_event(
            self.cycle,
            verdict,
            action,
            agent_timings,
            self.last_gate_findings,
            elapsed_seconds,
        )
        return verdict


    def _write_cycle_event(
        self,
        cycle: int,
        verdict: str,
        action: str,
        agent_timings: dict,
        gate_findings: str,
        cycle_duration_seconds: float,
    ):
        """Write structured JSON event for this cycle to .joshua/events/."""
        import json as _json

        events_dir = self.state_dir / "events"
        events_dir.mkdir(parents=True, exist_ok=True)
        cycle_dir = events_dir / f"cycle_{cycle:04d}"
        agents_dir = cycle_dir / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        event = {
            "cycle": cycle,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "verdict": verdict,
            "action": action,
            "cycle_duration_seconds": round(cycle_duration_seconds, 3),
            "agent_timings": agent_timings,
            "agents": self._current_cycle_agent_results,
            "approval": self._current_cycle_approval,
            "health": self._current_cycle_health,
            "gate_findings_chars": len(gate_findings),
            "stats": {
                "total_cycles": self.stats.get("go", 0)
                + self.stats.get("caution", 0)
                + self.stats.get("revert", 0)
                + self.stats.get("errors", 0),
                "go_count": self.stats.get("go", 0),
                "caution_count": self.stats.get("caution", 0),
                "revert_count": self.stats.get("revert", 0),
                "error_count": self.stats.get("errors", 0),
            }
        }
        event_file = events_dir / f"cycle_{cycle:04d}.json"
        event_file.write_text(_json.dumps(event, indent=2))
        cycle_file = cycle_dir / "event.json"
        cycle_file.write_text(_json.dumps(event, indent=2))
        for agent_name, payload in self._current_cycle_agent_results.items():
            agent_file = agents_dir / f"{agent_name}.json"
            agent_file.write_text(_json.dumps(payload, indent=2))

    def _stagger_wait(self, next_agent: str):
        """Wait between agent runs: memory check + fixed delay."""
        if self.min_memory_gb:
            if not wait_for_memory(self.min_memory_gb, timeout=120):
                log.warning(
                    f"Low memory before [{next_agent}] — running anyway")
        if self.agent_stagger:
            log.info(f"Stagger: waiting {self.agent_stagger}s before [{next_agent}]")
            self._wait_or_stop(self.agent_stagger)

    def _run_agent_with_retry(self, agent: Agent, task: str,
                               context: dict) -> RunResult:
        """Run agent with configurable retries."""
        self._last_agent_attempts = 1
        result = self._run_agent(agent, task, context)
        if result.success or not self.retries:
            return result

        for attempt in range(1, self.retries + 1):
            self._last_agent_attempts = attempt + 1
            log.info(f"[{agent.name}] Retry {attempt}/{self.retries}")
            if self._wait_or_stop(5 * attempt):
                return RunResult(
                    success=False,
                    output="",
                    exit_code=-1,
                    duration_seconds=0,
                    error="Cancelled",
                    error_type="cancelled",
                )
            result = self._run_agent(agent, task, context)
            if result.success:
                return result

        return result

    def _run_agent(self, agent: Agent, task: str, context: dict) -> RunResult:
        """Run a single agent with full prompt construction."""
        ctx = dict(context)
        if self.memory_enabled:
            ctx["memory"] = build_memory_prompt(agent.name, self.state_dir)
            ctx["wiki"] = build_wiki_context(
                self.project_name, task, str(self.state_dir / "wiki")
            )
        else:
            ctx["memory"] = ""
            ctx["wiki"] = ""

        system_prompt = agent.build_system_prompt(ctx)
        user_prompt = agent.build_task_prompt(task, self.cycle, ctx)

        result = self.runner.run(
            prompt=user_prompt,
            cwd=self.project_dir,
            system_prompt=system_prompt,
            timeout=self.runner.timeout,
        )

        log.info(
            f"[{agent.name}] {'OK' if result.success else 'FAIL'} "
            f"({result.duration_seconds}s, {len(result.output)} chars)"
        )
        return result

    def _record_result(self, agent: Agent, task: str, result: RunResult):
        """Save lessons and raw output after an agent run."""
        if not self.memory_enabled:
            return

        task = redact_secrets(task)
        output = redact_secrets(result.output)

        extract_lessons(
            agent_name=agent.name,
            task=task,
            output=output,
            success=result.success,
            cycle=self.cycle,
            state_dir=self.state_dir,
        )
        save_raw(
            agent=agent.name,
            cycle=self.cycle,
            task=task,
            content=output,
            project=self.project_name,
            wiki_dir=str(self.state_dir / "wiki"),
        )

    def _build_context(self) -> dict:
        """Build the context dict for prompt rendering."""
        ctx = {
            "project_name": self.project_name,
            "project_dir": self.project_dir,
            "deploy_command": self.deploy_cmd,
            "health_url": self.health_url,
            "site_url": self.site_url,
            "cycle": self.cycle,
            "gate_findings": "",
        }
        if self.cross_agent_context and self.last_gate_findings:
            ctx["gate_findings"] = (
                f"\n--- PREVIOUS QA FINDINGS ---\n{self.last_gate_findings}"
            )
        # External context (e.g., Brain knowledge base)
        if self.context_provider:
            try:
                ctx["external_context"] = self.context_provider(self.cycle)
            except Exception as e:
                log.warning(f"Context provider error: {e}")
                ctx["external_context"] = ""
        else:
            ctx["external_context"] = ""
        return ctx

    def _parse_verdict(self, output: str) -> str:
        """Parse gate agent verdict from output.

        Primary: JSON block with full structured contract:
          {"verdict": "GO|CAUTION|REVERT", "severity": "...",
           "findings": "...", "issues": [...], "recommended_action": "..."}

        Fallback 1: legacy ``VERDICT: GO`` line (deprecated, logs warning).
        Fallback 2: default CAUTION with truncated output for debugging.

        Sets self.last_verdict_source to "json" | "legacy" | "default"
        so callers and APIs can distinguish how the verdict was obtained.
        """
        # 1. JSON block — fenced (```json...```) or raw object, validated via GateVerdict
        from pydantic import ValidationError as _PydanticValidationError
        for pattern in (
            r"```json\s*(\{.*?\})\s*```",                 # fenced code block
            r"```\s*(\{[^`]*\"verdict\"[^`]*\})\s*```",  # generic fenced block
            r'(\{[^{}]*"verdict"\s*:[^{}]*\})',           # raw inline object
        ):
            json_match = re.search(pattern, output, re.DOTALL)
            if json_match:
                try:
                    data = json.loads(json_match.group(1))
                    gv = GateVerdict.model_validate(data)
                    self.last_gate_findings = gv.findings
                    self.last_gate_issues = gv.issues
                    self.last_gate_severity = gv.severity
                    self.last_gate_recommended_action = gv.recommended_action
                    self.last_gate_confidence = gv.confidence
                    self.last_verdict_source = "json"
                    self.sprint_logger.info(
                        f"Verdict: {gv.verdict} | source=json | "
                        f"severity={gv.severity} | issues={len(gv.issues)} | "
                        f"confidence={gv.confidence}"
                    )
                    return gv.verdict
                except _PydanticValidationError as e:
                    self.sprint_logger.warning(
                        f"GateVerdict validation failed — falling back to CAUTION. "
                        f"Errors: {e.error_count()} — {e.errors()[0]['msg'] if e.errors() else ''}"
                    )
                    # populate partial fields for debugging
                    try:
                        raw = json.loads(json_match.group(1))
                        self.last_gate_findings = raw.get("findings", output[:500])
                    except Exception:
                        self.last_gate_findings = output[:500]
                    self.last_gate_issues = []
                    self.last_gate_severity = "unknown"
                    self.last_gate_recommended_action = ""
                    self.last_verdict_source = "default"
                    return "CAUTION"
                except (json.JSONDecodeError, AttributeError):
                    pass

        # 2. Legacy VERDICT: line fallback
        match = re.search(r"VERDICT:\s*(GO|CAUTION|REVERT)", output, re.IGNORECASE)
        if match:
            verdict = match.group(1).upper()
            self.last_gate_severity = "unknown"
            self.last_gate_findings = output[:500]
            self.last_gate_issues = []
            self.last_gate_recommended_action = ""
            self.last_verdict_source = "legacy"
            log.warning(
                f"Verdict: {verdict} | source=legacy — gate agent should output JSON. "
                "Update the gate skill prompt."
            )
            return verdict

        # 3. Default CAUTION — log truncated output to help debug
        self.last_gate_severity = "unknown"
        self.last_gate_findings = output[:500]
        self.last_gate_issues = []
        self.last_gate_recommended_action = ""
        self.last_verdict_source = "default"
        log.warning(
            f"Could not parse verdict — defaulting to CAUTION. "
            f"Gate output (first 200 chars): {output[:200]!r}"
        )
        return "CAUTION"

    def _deploy(self, cmd: str | None = None, action: str = "deploy", check_approval: bool = True):
        """Run a deploy command safely (no shell=True)."""
        deploy_cmd = cmd or self.deploy_cmd
        if not deploy_cmd:
            return
        if self.no_deploy:
            self.sprint_logger.info(f"[no-deploy] Skipping {action}: {deploy_cmd}")
            return
        if check_approval and not self._request_approval(action, deploy_cmd):
            return
        success, output = run_command(
            deploy_cmd,
            cwd=self.project_dir,
            timeout=300,
            dry_run=False,
            cancel_event=self._stop_event,
            on_process_start=self._set_active_command,
            on_process_end=self._clear_active_command,
            allowed_commands=self.allowed_commands,
            allowed_paths=self.allowed_paths,
        )
        if not success and not self.no_deploy:
            log.error(f"Deploy failed: {output}")

    def _send_digest(self):
        """Send periodic digest via notifications and tracker."""
        lines = [
            f"Sprint Digest — {self.project_name}",
            f"Cycles: {max(1, self.cycle - self.digest_every + 1)}-{self.cycle}",
            f"Verdicts: GO={self.stats.get('go', 0)} "
            f"CAUTION={self.stats.get('caution', 0)} "
            f"REVERT={self.stats.get('revert', 0)} "
            f"Errors={self.stats.get('errors', 0)}",
        ]
        if self.cycle_summaries:
            lines.append("")
            for s in self.cycle_summaries[-self.digest_every:]:
                lines.append(
                    f"  C{s['cycle']}: {s['verdict']}")
        digest_text = "\n".join(lines)

        self.notifier.notify_event("digest", digest_text, self.project_name)
        self.tracker.create_issue(
            f"Sprint Digest — Cycles {max(1, self.cycle - self.digest_every + 1)}-{self.cycle}",
            digest_text,
        )
        log.info("Digest sent")

    def _save_checkpoint(self):
        """Save sprint state for resume."""
        checkpoint = {
            "cycle": self.cycle,
            "stats": self.stats,
            "timestamp": datetime.now().isoformat(),
            "project": self.project_name,
            "gate_blocked": self.gate_blocked,
            "last_gate_findings": self.last_gate_findings,
            "consecutive_errors": self.consecutive_errors,
        }
        path = self.state_dir / "checkpoint.json"
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(checkpoint, f, indent=2)
        tmp.rename(path)

    def _load_checkpoint(self) -> int:
        """Load sprint state for resume."""
        path = self.state_dir / "checkpoint.json"
        if path.exists():
            try:
                data = json.loads(path.read_text())
                self.stats = data.get("stats", self.stats)
                self.gate_blocked = data.get("gate_blocked", False)
                self.last_gate_findings = data.get("last_gate_findings", "")
                self.consecutive_errors = data.get("consecutive_errors", 0)
                cycle = data.get("cycle", 0)
                log.info(f"Resumed from checkpoint: cycle {cycle}")
                return cycle
            except Exception:
                pass
        return 0


def run_sprint(config_path: str):
    """Entry point: load config and run a sprint."""
    config = load_config(config_path)

    # Setup logging
    log_dir = Path(config["project"]["path"]) / ".joshua" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    handler = logging.FileHandler(log_dir / "sprint.log")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(console)
    log.setLevel(logging.INFO)

    sprint = Sprint(config)
    sprint.run()
