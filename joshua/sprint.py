"""Main sprint loop — the heart of joshua.

Orchestrates work skills → gate skills in continuous cycles.
Each cycle: pick tasks, run work agents, gate agents review, deploy or revert.
"""

import json
import logging
import os
import subprocess
import time
from datetime import datetime
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
from joshua.utils.preflight import run_preflight, check_memory, wait_for_memory

log = logging.getLogger("joshua")


class Sprint:
    """Autonomous multi-agent development sprint."""

    def __init__(self, config: dict):
        self.config = config
        self.project = config["project"]
        self.project_dir = self.project["path"]
        self.project_name = self.project["name"]
        self.deploy_cmd = self.project.get("deploy", "")
        self.health_url = self.project.get("health_url", "")

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

        # Production features
        self.gate_blocking = sprint_conf.get("gate_blocking", False)
        self.cross_agent_context = sprint_conf.get("cross_agent_context", False)
        self.health_check_enabled = sprint_conf.get("health_check", False)
        self.recovery_deploy = sprint_conf.get("recovery_deploy", "")
        self.retries = sprint_conf.get("retries", 0)
        self.max_consecutive_errors = sprint_conf.get("max_consecutive_errors", 0)
        self.git_strategy = sprint_conf.get("git_strategy", "none")
        self.agent_stagger = sprint_conf.get("agent_stagger", 0)  # seconds between agents
        self.min_memory_gb = sprint_conf.get("min_memory_gb", 0)  # wait for RAM before agent

        # Memory settings
        mem_conf = config.get("memory", {})
        self.memory_enabled = mem_conf.get("enabled", True)
        self.state_dir = Path(
            mem_conf.get("state_dir", os.path.join(self.project_dir, ".joshua"))
        )
        self.state_dir.mkdir(parents=True, exist_ok=True)

        # Hooks (set by server or external integrations)
        self._stop_requested = False
        self.on_cycle_complete = None  # callable(cycle_data: dict) -> None
        self.context_provider = None   # callable(cycle: int) -> str

        # State
        self.cycle = self._load_checkpoint()
        self.stats = {"go": 0, "caution": 0, "revert": 0, "errors": 0}
        self.cycle_summaries: list[dict] = []
        self.gate_blocked = False
        self.last_gate_findings = ""
        self.consecutive_errors = 0

    def stop(self):
        """Request graceful stop after current cycle completes."""
        self._stop_requested = True
        log.info("Stop requested — will finish after current cycle")

    def run(self):
        """Run the sprint loop until stopped, max_cycles, or max_hours reached."""
        log.info(f"Sprint started for {self.project_name} at {self.project_dir}")
        log.info(f"Runner: {self.runner.name} | Agents: {[a.name for a in self.agents]}")
        log.info(f"Cycle sleep: {self.cycle_sleep}s | Memory: {self.memory_enabled}")

        self.notifier.notify_event("start",
            f"Sprint started — {len(self.agents)} agents, runner={self.runner.name}",
            self.project_name)

        start_time = time.monotonic()

        try:
            while not self._stop_requested:
                self.cycle += 1

                if self.max_cycles and self.cycle > self.max_cycles:
                    log.info(f"Reached max_cycles ({self.max_cycles}). Stopping.")
                    break

                if self.max_hours:
                    elapsed_h = (time.monotonic() - start_time) / 3600
                    if elapsed_h >= self.max_hours:
                        log.info(f"Reached max_hours ({self.max_hours}h). Stopping.")
                        break

                # Pre-flight checks
                warnings = run_preflight(self.config)
                for w in warnings:
                    log.warning(f"Preflight: {w}")

                try:
                    verdict = self._run_cycle()
                    self.consecutive_errors = 0
                except Exception as e:
                    self.consecutive_errors += 1
                    self.stats["errors"] += 1
                    log.error(f"Cycle {self.cycle} error: {e}")
                    self.notifier.notify_event("crash",
                        f"Cycle {self.cycle} error: {str(e)[:200]}",
                        self.project_name)

                    if (self.max_consecutive_errors
                            and self.consecutive_errors >= self.max_consecutive_errors):
                        log.critical(
                            f"{self.consecutive_errors} consecutive errors. Stopping.")
                        break

                    # Exponential backoff
                    backoff = min(self.cycle_sleep * (2 ** self.consecutive_errors), 900)
                    log.info(f"Backing off {backoff}s...")
                    time.sleep(backoff)
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
                        log.warning(f"Cycle callback error: {e}")

                # Digest report
                if self.digest_every and self.cycle % self.digest_every == 0:
                    self._send_digest()

                # Sleep (longer after REVERT)
                sleep_time = self.revert_sleep if verdict == "REVERT" else self.cycle_sleep
                log.info(f"Sleeping {sleep_time}s before next cycle...")
                time.sleep(sleep_time)

        except KeyboardInterrupt:
            log.info("Sprint interrupted by user")
        finally:
            self._save_checkpoint()
            log.info(f"Sprint ended at cycle {self.cycle}. Stats: {self.stats}")
            self.notifier.notify_event("stop",
                f"Ended at cycle {self.cycle}. Stats: {self.stats}",
                self.project_name)

    def _run_cycle(self) -> str:
        """Execute one full cycle. Returns verdict string."""
        log.info(f"{'='*60}")
        log.info(f"CYCLE {self.cycle} — {datetime.now().isoformat(timespec='seconds')}")
        log.info(f"{'='*60}")

        # Health check
        if self.health_check_enabled and self.health_url:
            if not check_health(self.health_url):
                log.warning("Health check failed — attempting recovery")
                if self.recovery_deploy:
                    self._deploy(self.recovery_deploy)
                    time.sleep(10)
                if not check_health(self.health_url):
                    log.error("Still unhealthy after recovery — skipping cycle")
                    self.notifier.notify_event("health_fail",
                        f"Cycle {self.cycle} skipped — service unhealthy",
                        self.project_name)
                    return "CAUTION"

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
        for i, agent in enumerate(work_agents):
            # Stagger: wait between agents (skip before first)
            if i > 0:
                self._stagger_wait(agent.name)
            task = agent.get_task(self.cycle)
            log.info(f"[{agent.name}] ({agent.skill}) Task: {task[:80]}")
            result = self._run_agent_with_retry(agent, task, context)
            output = result.output if result.success else f"[FAILED] {result.error}"
            work_outputs[agent.name] = output
            self._record_result(agent, task, result)

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

            log.info(f"[{agent.name}] ({agent.skill}) Reviewing cycle {self.cycle}...")
            result = self._run_agent_with_retry(agent, gate_task, context)
            verdict = self._parse_verdict(result.output)
            self._record_result(agent, f"gate-cycle-{self.cycle}", result)

            # Store gate findings for cross-agent context
            if self.cross_agent_context:
                self.last_gate_findings = result.output[:2000]

        # Apply verdict
        self.stats[verdict.lower()] = self.stats.get(verdict.lower(), 0) + 1
        log.info(f"VERDICT: {verdict}")

        if verdict == "REVERT":
            log.warning("REVERT — changes will not be deployed")
            if self.gate_blocking:
                self.gate_blocked = True
            if branch and self.git_strategy == "snapshot":
                self.git.revert(branch)
            self.notifier.notify_event("revert",
                f"Cycle {self.cycle} REVERTED", self.project_name)
        else:
            self.gate_blocked = False
            if branch and self.git_strategy == "snapshot" and self.git.is_repo():
                self.git.merge_to_main(branch)
            if self.deploy_cmd and verdict in ("GO", "CAUTION"):
                if verdict == "CAUTION":
                    log.warning("CAUTION — deploying but flagging for review")
                self._deploy()

        # Summary
        self.cycle_summaries.append({
            "cycle": self.cycle,
            "verdict": verdict,
            "timestamp": datetime.now().isoformat(),
        })

        log.info(f"CYCLE {self.cycle} COMPLETE — verdict={verdict}")
        return verdict

    def _stagger_wait(self, next_agent: str):
        """Wait between agent runs: memory check + fixed delay."""
        if self.min_memory_gb:
            if not wait_for_memory(self.min_memory_gb, timeout=120):
                log.warning(
                    f"Low memory before [{next_agent}] — running anyway")
        if self.agent_stagger:
            log.info(f"Stagger: waiting {self.agent_stagger}s before [{next_agent}]")
            time.sleep(self.agent_stagger)

    def _run_agent_with_retry(self, agent: Agent, task: str,
                               context: dict) -> RunResult:
        """Run agent with configurable retries."""
        result = self._run_agent(agent, task, context)
        if result.success or not self.retries:
            return result

        for attempt in range(1, self.retries + 1):
            log.info(f"[{agent.name}] Retry {attempt}/{self.retries}")
            time.sleep(5 * attempt)
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

        extract_lessons(
            agent_name=agent.name,
            task=task,
            output=result.output,
            success=result.success,
            cycle=self.cycle,
            state_dir=self.state_dir,
        )
        save_raw(
            agent=agent.name,
            cycle=self.cycle,
            task=task,
            content=result.output,
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
        """Extract GO/CAUTION/REVERT from QA output."""
        for line in output.split("\n"):
            line = line.strip().upper()
            if line.startswith("VERDICT:"):
                v = line.split(":", 1)[1].strip()
                if v in ("GO", "CAUTION", "REVERT"):
                    return v
        return "CAUTION"  # safe default

    def _deploy(self, cmd: str | None = None):
        """Run a deploy command."""
        deploy_cmd = cmd or self.deploy_cmd
        if not deploy_cmd:
            return
        log.info(f"Deploying: {deploy_cmd}")
        try:
            result = subprocess.run(
                deploy_cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=300,
                cwd=self.project_dir,
            )
            if result.returncode == 0:
                log.info("Deploy successful")
            else:
                log.error(f"Deploy failed: {result.stderr[:500]}")
        except Exception as e:
            log.error(f"Deploy error: {e}")

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
