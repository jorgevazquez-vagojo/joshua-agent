# Changelog

All notable changes to joshua-agent are documented here.

## [0.7.0] — 2026-04-08

### Added
- **Real-time log streaming** (`GET /sprints/{id}/logs/stream`): SSE endpoint streams sprint logs live, replacing the need to poll `/logs`.
- **Verdict history** (`GET /sprints/{id}/verdicts`): structured list of every cycle verdict parsed from `results.tsv`.
- **Sprint report** (`GET /sprints/{id}/report`): aggregated summary with verdict counts, avg duration, worst cycle, trend (`improving`/`degrading`/`stable`), and cost estimate in USD.
- **`GateTaskSource`**: dynamic task source (`task_source: gate`) that generates the next agent task from the last gate findings — prioritises REVERT/CAUTION issues automatically.
- **Lesson expiry** (`memory.max_lesson_age_cycles`, default 50): stale lessons are filtered out of the memory prompt, keeping context clean over long runs.
- **Token cost tracking**: `RunResult.tokens_out` estimated as `len(output) // 4`; accumulated per-cycle in `stats.total_tokens`; exposed in the sprint report with a USD estimate (Sonnet pricing: $3/MTok output).
- **`allowed_paths`** in `run_command()`: restrict deploy/revert scripts to a declared path list; previously missing parameter that blocked related tests.
- **Transient vs terminal error classification** (`RunResult.is_transient()` / `is_terminal()`): transient errors (timeout, rate-limit) trigger a 30 s backoff + one automatic retry; terminal errors (binary not found, cancellation) stop the sprint immediately.

## [0.6.1] — 2026-04-06

### Fixed
- **Config contract unified**: schema now matches what the runtime reads.
  `project.deploy` and `project.health_url` in `project:`;
  `sprint.cycle_sleep`, `sprint.revert_sleep`, `sprint.health_check` in `sprint:`.
  Removed dead fields (`deploy_command`, `revert_command`, `health_check_command`, `cycle_delay`).
- Schema `extra: "allow"` → `"ignore"` at top level; added `PreflightConfig` and `TrackerConfig`.
- Shell metacharacter validation on `project.deploy` and config `${VAR:default}` values.
- README deploy safety warning references `project.deploy`, not `deploy_command`.

### Security
- DB files restricted to 0600/0700 (was world-readable 0644).
- Worker errors redacted before storing in SQLite.
- Shell injection regex blocks `$VAR`, `${}`, newlines (was only `; | \` $()`).
- SSRF: health checks, hub callbacks, Slack/webhook notifiers validate URLs against private IPs.
- Jira task source enforces HTTPS (credentials via Basic Auth).
- Server `INTERNAL_TOKEN` requires minimum 16 characters.
- Notification error logs redact tokens and webhook URLs.
- Sprint log API redacts secrets before returning lines.
- FastAPI: rate limiting (30 req/min/token), security headers (nosniff, DENY, no-store).

## [0.6.0] — 2026-04-06

### Added
- **Process-based runtime**: each sprint runs in its own `multiprocessing.Process`.
- **SQLite persistence** (`SprintDB`): durable sprint state, WAL mode, survives restarts.
- **ProcessManager**: start/stop/list sprints with real process lifecycle.
- **Supervisor thread**: detects zombie sprints, cleans up leaked processes.
- **Task source hooks**: `TaskSource` ABC with `JiraTaskSource` for dynamic Jira task fetching.
- Hub callback: POST sprint status to external systems on lifecycle events.

### Changed
- Demo: WarGames characters (Falken, Jennifer, McKittrick), FastAPI Task Manager.
- Removed `lightman`/`vulcan`/`wopr` skill templates (redundant copies).
- `from __future__ import annotations` across all modules.
- Classifier: Alpha → Beta.

## [0.5.0] — 2026-04-06

### Security
- **SSRF protection**: DNS resolution + `ipaddress` validation blocks private IPs, IPv6 ULA, DNS rebinding.
- **Timing-safe auth**: `hmac.compare_digest()` for token comparison.
- **Supply chain**: `pypa/gh-action-pypi-publish` pinned to SHA.
- **CI permissions**: `contents: read` only. Lint mandatory.

### Reliability
- Graceful shutdown: server stops all running sprints on SIGTERM with 30s timeout.
- Registry cleanup: finished sprints evicted from in-memory registry (prevents leaks).
- Lock file properly closed in `finally` block and removed on exit.

### Housekeeping
- Version from `importlib.metadata` (single source of truth).
- `datetime.utcnow()` → `datetime.now(timezone.utc)` (Python 3.12 compat).
- Path traversal fix in `FilesystemTracker.add_comment()`.

## [0.4.0] — 2026-04-06

### Added
- **SQLite sprint persistence**: `SprintDB` tracks full lifecycle, orphaned sprints marked `interrupted` on restart.
- **Strict gate verdict contract**: `GateVerdict` Pydantic model, `verdict_source` field (`json`|`legacy`|`default`).
- **Per-sprint log files**: `RotatingFileHandler` (10MB × 3), `GET /sprints/{id}/logs` endpoint.
- `GATE_JSON_SCHEMA` embedded in all gate prompts (single source of truth).

## [0.3.0] — 2026-04-06

### Security
- `bash -c` / `sh -c` rejected in `safe_cmd.py` (equivalent to `shell=True`).
- Shell interpreters may only run script files (`bash ./deploy.sh`).
- `project.path` required — returns 422 if omitted.

### Improved
- Verdict parser: 3 JSON patterns (fenced code block, generic, raw inline).
- `last_gate_*` fields always populated (including fallback paths).
- Default CAUTION fallback logs truncated gate output for debugging.

## [0.2.0] — 2026-04-06

### Security
- `CustomRunner`: removed `shell=True`, uses `shlex.split()` (RCE prevention).
- Server: removed `X-Internal-Token` forwarding to callback URLs (token exfiltration).

### Fixed
- `git.py snapshot()`: stash restored with `git stash pop` on failure.
- `agents.py get_task()`: cycle 1 correctly maps to `tasks[0]`.

### Tests
- 18 dedicated HTTP endpoint tests (auth, SSRF, sprint lifecycle).

## [0.1.0] — 2026-04-06

### Added
- Multi-agent sprint loop with GO/CAUTION/REVERT verdicts.
- Skills system: dev, qa, bug-hunter, security, perf, pm, tech-writer, or any custom skill.
- Runners: Claude Code, Aider, Codex, Custom command template.
- Self-learning wiki: lessons per cycle, synthesized daily (`joshua evolve`).
- HTTP control plane: `joshua serve` REST API.
- Notifications: Telegram, Slack, Webhook with circuit breaker.
- Git integration: auto-detect branches, snapshot per cycle.
- Checkpointing, structured cycle events, Pydantic v2 validation.
- Rate limiting, output truncation, file locks, log rotation.
- `--no-deploy`, `--dry-run` CLI flags. SIGTERM/SIGINT shutdown.
- Examples: python-api, nextjs, wordpress, minimal, full-team, executive-team, legal-review.
