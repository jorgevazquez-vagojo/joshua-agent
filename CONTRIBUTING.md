# Contributing to joshua-agent

## Getting started

```bash
git clone https://github.com/jorgevazquez-vagojo/joshua-agent.git
cd joshua-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[server,dev]'
```

## Running tests

```bash
pytest tests/ -q
```

All PRs must pass the full test suite. No regressions.

## Adding a new runner

1. Create `joshua/runners/your_runner.py` extending `LLMRunner`
2. Implement `_run_impl()` and `name` property
3. Register in `joshua/runners/__init__.py`
4. Add tests in `tests/test_runners.py`

## Adding a new skill

Skills live in `joshua/skills/`. Each skill is a YAML file with a `system_prompt` template. See existing skills for reference.

## Code style

```bash
ruff check joshua/ tests/
```

Line length: 100. Target: Python 3.11+.

## Pull requests

- One logical change per PR
- Tests required for new behavior
- Update CHANGELOG.md under `[Unreleased]`
- Commit messages: `type: short description` (feat/fix/refactor/chore/docs)

## Reporting issues

Use [GitHub Issues](https://github.com/jorgevazquez-vagojo/joshua-agent/issues). Include your config YAML (redact secrets), runner type, and the error output.
