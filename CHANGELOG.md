# Changelog

All notable changes to `joshua-agent` are documented here.

## [0.6.0] — 2026-04-06

### Changed
- Repositioned the README around verifiable product signals instead of pure editorial narrative
- Reworked the Quick Start to be safe by default, with `./deploy.sh` as the first deploy pattern
- Added explicit `Current status` / `Estado actual` sections to separate stable capabilities from experimental ones
- Promoted public package maturity from Alpha to Beta in `pyproject.toml`

### Fixed
- HTTP server now tolerates lightweight sprint stubs that do not implement per-sprint file logging, which restores the `tests/test_server.py` start/stop path

## [0.5.0] — 2026-04-06

### Added
- Strict GitHub Actions CI across Python `3.11`, `3.12`, and `3.13`
- Real HTTP server tests and the dev dependencies needed to run them locally
- Graceful shutdown paths for long-running sprints

### Security
- Full callback and control-plane hardening, including stronger SSRF defenses
- Safer CLI defaults and tighter request validation on the HTTP server
- Hardened sprint cancellation paths

## [0.4.0] — 2026-04-06

### Added
- Persistent sprint state across cycles
- Explicit gate contract for `GO`, `CAUTION`, and `REVERT`
- Per-sprint log files for auditability and postmortems

## [0.3.0] — 2026-04-06

### Added
- Pydantic config validation with clearer errors
- Structured cycle event output
- `--no-deploy` flag for safer dry runs on live projects

### Changed
- Public docs and examples were overhauled for external use

## [0.2.0] — 2026-04-06

### Added
- Safe command execution defaults
- SSRF guardrails and rate limiting
- JSON verdict handling for gate agents

### Changed
- Package URLs, author metadata, and public-release positioning

## [0.1.0] — 2026-04-06

### Added
- Multi-agent sprint loop: work agents run in cycles, gate agent issues `GO` / `CAUTION` / `REVERT`
- Skills system: define agent roles in YAML (`dev`, `qa`, `legal`, `cfo`, or anything else)
- Runners for Claude Code CLI, Aider, Codex, and custom command templates
- Quality gate: `GO` auto-deploys, `CAUTION` continues, `REVERT` rolls back via git
- Self-learning wiki: lessons extracted per cycle, wiki synthesized later via `joshua evolve`
- HTTP control plane: `joshua serve` exposes a REST API for sprint management
- Notifications: Telegram, Slack, and webhook backends
- Git integration: auto-detect `main` / `master` / `develop`, snapshot branches per cycle
- Checkpointing: resume a sprint after a crash via `.joshua/checkpoint.json`
- Structured cycle events in `.joshua/events/cycle_NNNN.json`
- Output truncation for oversized agent responses
- File locking to prevent concurrent sprints on the same `.joshua` directory
- `--dry-run` CLI flag for config validation without execution
- Log rotation in `.joshua/logs/`
- Example configs for Python API, Next.js, WordPress, minimal, full-team, executive-team, and legal-review
