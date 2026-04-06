"""Safe shell command executor — no shell=True, allowlist, secret redaction."""
from __future__ import annotations

import logging
import os
import re
import shlex
import signal
import subprocess
import time
from typing import Optional

log = logging.getLogger("joshua")

# Commands allowed as the first token in a deploy/revert/health-check command.
# Users who need complex pipelines should wrap them in a shell script.
ALLOWED_COMMANDS = {
    "git", "docker", "docker-compose", "kubectl", "helm",
    "npm", "npx", "yarn", "pnpm", "bun",
    "python", "python3", "pip", "pip3", "uv",
    "make", "bash", "sh", "zsh", "fish",
    "rsync", "ssh", "scp",
    "systemctl", "service",
    "cargo", "go",
    "ansible", "ansible-playbook",
    "flyctl", "heroku", "vercel", "railway",
}

# Shell interpreters that accept -c <string> — must not be used that way
# (equivalent to shell=True). Only allow them to run script files.
_SHELL_INTERPRETERS = {"bash", "sh", "zsh", "fish"}

# Patterns that look like secrets in env vars — redact from logs
_SECRET_PATTERN = re.compile(
    r"(TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|PRIVATE_KEY|AUTH|CREDENTIAL)",
    re.IGNORECASE,
)


def _redact_env(env: dict) -> dict:
    """Return env dict with secret values replaced by ***."""
    return {
        k: "***" if _SECRET_PATTERN.search(k) else v
        for k, v in env.items()
    }


def _safe_parse(cmd: str) -> list[str]:
    """Parse command string into argv list. Raises ValueError on dangerous patterns."""
    if not cmd or not cmd.strip():
        raise ValueError("Empty command")

    # Detect shell metacharacters that only make sense with shell=True
    dangerous = re.search(r"[;&|`]|\$\(", cmd)
    if dangerous:
        raise ValueError(
            f"Command contains shell metacharacter '{dangerous.group()}'. "
            "Wrap complex commands in a script file (e.g. bash ./deploy.sh) "
            "and reference that instead."
        )

    args = shlex.split(cmd)
    if not args:
        raise ValueError("Empty command after parsing")

    # Validate first token
    first = os.path.basename(args[0])  # handle /usr/bin/docker -> docker
    if first not in ALLOWED_COMMANDS and not args[0].startswith(("./", "/", "~/")):
        raise ValueError(
            f"Command '{first}' is not in the allowed list. "
            f"Allowed: {sorted(ALLOWED_COMMANDS)}. "
            "Use a full path or add your tool to JOSHUA_ALLOWED_COMMANDS env var."
        )

    # Block shell interpreters invoked with -c (equivalent to shell=True)
    # Allow: bash ./deploy.sh, sh /opt/run.sh
    # Reject: bash -c "...", sh -c "..."
    if first in _SHELL_INTERPRETERS and len(args) > 1 and args[1] == "-c":
        raise ValueError(
            f"'{first} -c' is not allowed — it is equivalent to shell=True. "
            "Put your commands in a script file and run: bash ./your-script.sh"
        )

    return args


def run_command(
    cmd: str,
    cwd: str,
    timeout: int = 300,
    dry_run: bool = False,
    extra_env: Optional[dict] = None,
    cancel_event=None,
    on_process_start=None,
    on_process_end=None,
) -> tuple[bool, str]:
    """
    Run a deploy/revert command safely.

    - No shell=True
    - Allowlist on first token
    - Blocks shell metacharacters
    - Redacts secrets from logs
    - Returns (success, output)
    """
    try:
        args = _safe_parse(cmd)
    except ValueError as e:
        log.error(f"Command rejected: {e}")
        return False, str(e)

    # Build environment: inherit from process but allow extension
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)

    log.info(f"Running: {' '.join(args)} (cwd={cwd})")
    if dry_run:
        log.info("[dry-run] Command not executed")
        return True, "[dry-run] skipped"

    try:
        process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
            env=env,
            start_new_session=(os.name != "nt"),
        )
        if on_process_start:
            on_process_start(process)

        start = time.monotonic()
        timed_out = False
        cancelled = False
        while True:
            try:
                stdout, stderr = process.communicate(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                if cancel_event and cancel_event.is_set():
                    cancelled = True
                    _terminate_process(process)
                    stdout, stderr = process.communicate()
                    break
                if time.monotonic() - start >= timeout:
                    timed_out = True
                    _terminate_process(process)
                    stdout, stderr = process.communicate()
                    break

        if cancelled:
            log.info("Command cancelled")
            return False, "Cancelled"
        if timed_out:
            log.error(f"Command timed out after {timeout}s")
            return False, f"Timeout after {timeout}s"
        if process.returncode == 0:
            return True, stdout.strip()

        # Redact secrets from error output before logging
        stderr = stderr[:1000]
        for k, v in env.items():
            if _SECRET_PATTERN.search(k) and v and len(v) > 4:
                stderr = stderr.replace(v, "***")
        log.error(f"Command failed (exit {process.returncode}): {stderr}")
        return False, stderr
    except FileNotFoundError:
        log.error(f"Command not found: {args[0]}")
        return False, f"Command not found: {args[0]}"
    finally:
        if 'process' in locals() and on_process_end:
            on_process_end(process)


def extend_allowlist(extra: list[str]) -> None:
    """Add extra commands to the allowlist (e.g. from JOSHUA_ALLOWED_COMMANDS env var)."""
    ALLOWED_COMMANDS.update(extra)


# Extend allowlist from env var: JOSHUA_ALLOWED_COMMANDS=myapp,mytool
_extra = os.environ.get("JOSHUA_ALLOWED_COMMANDS", "")
if _extra:
    extend_allowlist([c.strip() for c in _extra.split(",") if c.strip()])


def _terminate_process(process: subprocess.Popen[str]):
    """Terminate a running command safely."""
    if process.poll() is not None:
        return
    try:
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
    except ProcessLookupError:
        return
    except OSError:
        process.terminate()
