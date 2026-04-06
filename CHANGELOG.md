# Changelog

All notable changes to joshua-agent are documented here.

## [0.1.0] — 2026-04-06

### Added
- Multi-agent sprint loop: work agents run in cycles, gate agent issues GO/CAUTION/REVERT verdicts
- Skills system: define agent roles in YAML (dev, qa, legal, cfo — anything)
- Runners: Claude Code CLI, Aider, Codex, Custom command template
- Quality gate: GO auto-deploys, CAUTION continues, REVERT rolls back via git
- Self-learning wiki: lessons extracted per cycle, wiki synthesized daily (`joshua evolve`)
- HTTP control plane: `joshua serve` exposes REST API for programmatic sprint management
- Notifications: Telegram, Slack, Webhook backends with circuit breaker
- Git integration: auto-detect main/master/develop, snapshot branches per cycle
- Checkpointing: resume sprint after crash via `.joshua/checkpoint.json`
- Structured cycle events: `.joshua/events/cycle_NNNN.json` per cycle
- Pydantic v2 config validation with clear error messages
- Rate limiting: `requests_per_minute` config on all runners
- Output truncation: outputs >50k chars capped to avoid token limit issues
- File lock: prevents concurrent sprints on the same `.joshua` directory
- `--no-deploy` CLI flag: skip deploy_command even on GO verdict
- `--dry-run` CLI flag: validate config without running
- Log rotation: 100MB × 5 backups in `.joshua/logs/`
- SIGTERM/SIGINT graceful shutdown
- Examples: python-api, nextjs, wordpress, minimal, full-team, executive-team, legal-review
