# Primor — Production Setup Guide

Deploy guide for running joshua-agent QA sprints against **primor.eu**.

Two sprints are available:
- `examples/primor-magento.yaml` — Live site QA + Magento 2 codebase audit
- `examples/primor-mobile.yaml` — Mobile app REST/GraphQL API testing

---

## Prerequisites

- Python 3.11+ (system or venv)
- `claude` CLI authenticated (`claude --version`)
- Telegram bot + chat ID (for notifications)
- Access to the Magento codebase (for `magento-hunter` — optional)

---

## Install

```bash
pip install joshua-agent
# or from source:
git clone https://github.com/jorgevazquez-vagojo/joshua-agent
cd joshua-agent
pip install -e .
```

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_TOKEN` | Yes | Telegram bot token |
| `TELEGRAM_CHAT_ID` | Yes | Telegram chat ID for notifications |
| `PRIMOR_PATH` | No | Base dir for Magento sprint (default: `~/primor-magento`) |
| `PRIMOR_MOBILE_PATH` | No | Base dir for mobile sprint (default: `~/primor-mobile`) |
| `ANTHROPIC_API_KEY` | Yes | Anthropic API key (or `claude` CLI auth) |

Create a `.env` file or export before running:

```bash
export TELEGRAM_TOKEN="your-bot-token"
export TELEGRAM_CHAT_ID="your-chat-id"
export PRIMOR_PATH="$HOME/primor-magento"
```

---

## Setup directories

```bash
# Magento sprint
mkdir -p ~/primor-magento/reports

# If you have the Magento codebase, point PRIMOR_PATH to the Magento root:
export PRIMOR_PATH="/path/to/primor/magento-root"

# Mobile sprint
mkdir -p ~/primor-mobile/reports
export PRIMOR_MOBILE_PATH="$HOME/primor-mobile"
```

The `reports/` directory is where agents write their findings each cycle. The gate reads from here before issuing its verdict.

---

## Run

### Magento QA sprint (site + codebase)

```bash
joshua run examples/primor-magento.yaml
```

**What runs:**
1. `probe` (researcher) — tests live primor.eu with curl
2. `vulcan-m2` (magento-hunter) — audits the Magento PHP codebase
3. `sentinel` (ecommerce-qa) — reads both reports, issues GO/CAUTION/REVERT

If `PRIMOR_PATH` points to an empty directory, `probe` will still run (live site testing works without codebase). `vulcan-m2` will report "no codebase found" and skip — sentinel will still issue a verdict based on probe's findings.

### Mobile API sprint

```bash
joshua run examples/primor-mobile.yaml
```

**What runs:**
1. `apitester` (mobile-tester) — tests Magento REST endpoints via curl
2. `mobile-probe` (researcher) — simulates mobile browser navigation
3. `sentinel-mobile` (ecommerce-qa) — reviews all findings

---

## Sprint behavior

| Setting | Magento | Mobile | Effect |
|---|---|---|---|
| `cycle_sleep` | 900s | 600s | Wait between cycles |
| `max_cycles` | 20 | 30 | Stop after N cycles |
| `gate_blocking` | true | true | Work agents pause on REVERT |
| `cross_agent_context` | true | true | Hunter receives researcher's findings |
| `git_strategy` | none | none | No git ops — read-only |

At REVERT verdict: Telegram alert is sent, work agents are blocked for next cycle. The sprint continues (agents still run on the following cycle) — no code is modified or deployed.

---

## Check status

```bash
# While running in background:
joshua status examples/primor-magento.yaml

# View accumulated reports:
ls ~/primor-magento/reports/
cat ~/primor-magento/reports/cycle-1.md
```

---

## Notifications

Telegram alerts are sent:
- Every REVERT verdict — includes critical findings summary
- Sprint start/stop
- Consecutive errors (after 3)

---

## Stopping a sprint

```bash
# Graceful stop (finishes current cycle):
Ctrl+C

# Or send SIGTERM to the process
```

---

## Tuning

**Speed up cycles** (for initial testing):
```yaml
sprint:
  cycle_sleep: 60    # 1 min between cycles
  max_cycles: 3      # Quick 3-cycle test run
```

**Add more tasks to researcher:**
```yaml
agents:
  researcher:
    skill: researcher
    tasks:
      - "Test the promotions page at /promotiones and /ofertas..."
      - "Check the gift card flow..."
```

**Increase max_changes for deeper scans:**
```yaml
agents:
  magento-hunter:
    skill: magento-hunter
    max_changes: 10   # Find up to 10 bugs per cycle (default: 5)
```

---

## Troubleshooting

**"project path does not exist"** — Create the directory: `mkdir -p $PRIMOR_PATH/reports`

**"claude not found"** — Install and authenticate the Claude CLI: `pip install claude-code && claude --login`

**Gate always issues CAUTION** — Normal for first few cycles. The gate calibrates as lessons accumulate in `.joshua/wiki.md`.

**Slow response times from primor.eu** — Expected during peak hours. The researcher flags >3s as slow; the gate escalates to CAUTION only if >5s or checkout-critical pages.
