#!/usr/bin/env bash
# ============================================================================
# joshua-agent v0.6.0 — Interactive Demo
# "Shall we play a game?"
#
# Showcases: Start → Verdict JSON → Deploy/Revert → Logs → History
# No real LLM calls — uses a mock runner for instant results.
# ============================================================================

set -euo pipefail

# ── Colors ─────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'
CYAN='\033[0;36m'; BOLD='\033[1m'; DIM='\033[2m'; NC='\033[0m'

# ── Helpers ────────────────────────────────────────────────────────────
banner() { echo -e "\n${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"; }
step()   { echo -e "\n${BOLD}${CYAN}[$1]${NC} $2"; }
ok()     { echo -e "  ${GREEN}✓${NC} $1"; }
warn()   { echo -e "  ${YELLOW}⚠${NC} $1"; }
fail()   { echo -e "  ${RED}✗${NC} $1"; }
pause()  { echo -e "\n${DIM}  Press Enter to continue...${NC}"; read -r; }
json()   { echo -e "${DIM}$1${NC}"; }

DEMO_DIR=$(mktemp -d)
PROJECT_DIR="$DEMO_DIR/demo-project"
STATE_DIR="$PROJECT_DIR/.joshua"
EVENTS_DIR="$STATE_DIR/events"
LOGS_DIR="$STATE_DIR/logs"
mkdir -p "$PROJECT_DIR" "$EVENTS_DIR" "$LOGS_DIR"

# Create a fake git repo for the demo
cd "$PROJECT_DIR"
git init -q
echo "# Demo API" > README.md
cat > app.py << 'PYEOF'
from flask import Flask, request, jsonify

app = Flask(__name__)

# TODO: Fix SQL injection vulnerability
def get_user(user_id):
    query = f"SELECT * FROM users WHERE id = {user_id}"  # VULNERABLE!
    return query

SECRET_KEY = "hardcoded-secret-12345"  # VULNERABLE!

@app.route("/users/<user_id>")
def user(user_id):
    return jsonify({"query": get_user(user_id)})

if __name__ == "__main__":
    app.run(debug=True)  # debug=True in production!
PYEOF
git add -A && git commit -q -m "initial: vulnerable demo app"

trap 'rm -rf "$DEMO_DIR"' EXIT

# ============================================================================
clear
echo -e "${BOLD}"
cat << 'ASCII'
     ╦╔═╗╔═╗╦ ╦╦ ╦╔═╗
     ║║ ║╚═╗╠═╣║ ║╠═╣
    ╚╝╚═╝╚═╝╩ ╩╚═╝╩ ╩
    "Shall we play a game?"
ASCII
echo -e "${NC}"
echo -e "  ${DIM}Autonomous Gated Software Sprints — v0.6.0${NC}"
echo -e "  ${DIM}Demo: Start → Verdict → Deploy/Revert → Logs → History${NC}"
banner

pause

# ── STEP 1: YAML Config ────────────────────────────────────────────────
step "1/6" "YAML Config — Define your team"

cat << 'YAMLEOF'

  project:
    name: demo-api
    path: ~/demo-project

  runner:
    type: claude            # or: aider, codex, custom
    timeout: 90

  agents:
    lightman:               # Dev agent
      skill: dev
      tasks:
        - "Fix SQL injection in app.py"
        - "Remove hardcoded SECRET_KEY"
    vulcan:                 # Bug hunter
      skill: bug-hunter
      tasks:
        - "Find missing input validation"
    wopr:                   # QA gate
      skill: qa

  sprint:
    max_cycles: 3
    deploy_command: "./deploy.sh"
    gate_blocking: true     # REVERT blocks dev until fixed

YAMLEOF
ok "Config defines 3 agents: Lightman (dev), Vulcan (bug-hunter), WOPR (qa)"
ok "Gate blocking ON — REVERT verdict blocks further development"

pause

# ── STEP 2: Sprint Start ───────────────────────────────────────────────
step "2/6" "Sprint Start — Cycle 1 begins"

echo -e "\n  ${DIM}\$ joshua run demo-sprint.yaml${NC}\n"
sleep 0.5
echo -e "  2026-04-06 10:00:01 INFO ${GREEN}Sprint started${NC}: demo-api"
echo -e "  2026-04-06 10:00:01 INFO Cycle ${BOLD}1${NC}/3"
sleep 0.3
echo -e "  2026-04-06 10:00:02 INFO ${CYAN}[lightman]${NC} Running task: Fix SQL injection in app.py"
sleep 0.5
echo -e "  2026-04-06 10:00:45 INFO ${CYAN}[lightman]${NC} Done (43s) — parameterized queries applied"
sleep 0.3
echo -e "  2026-04-06 10:00:46 INFO ${CYAN}[vulcan]${NC}   Running task: Find missing input validation"
sleep 0.5
echo -e "  2026-04-06 10:01:20 INFO ${CYAN}[vulcan]${NC}   Done (34s) — found 2 issues, applied fixes"
sleep 0.3
echo -e "  2026-04-06 10:01:21 INFO ${YELLOW}[wopr]${NC}     Gate review starting..."
sleep 0.5

ok "Work agents completed → gate agent reviewing changes"

pause

# ── STEP 3: Verdict JSON ───────────────────────────────────────────────
step "3/6" "Gate Verdict — WOPR reviews all changes"

echo ""
VERDICT_GO='{
    "verdict": "GO",
    "severity": "none",
    "findings": "SQL injection fixed with parameterized queries. SECRET_KEY moved to env var. Input validation added to all endpoints. All changes are safe and correct.",
    "issues": [],
    "recommended_action": "Next cycle: add rate limiting and request logging.",
    "confidence": 0.92
}'

echo -e "  ${YELLOW}WOPR verdict:${NC}"
echo ""
echo "$VERDICT_GO" | python3 -m json.tool 2>/dev/null | while IFS= read -r line; do
    echo -e "  ${GREEN}$line${NC}"
done

echo ""
ok "Verdict: ${GREEN}GO${NC} — confidence 0.92"
ok "Gate passed — deploying changes"

# Save event
cat > "$EVENTS_DIR/cycle_0001.json" << EOF
{
    "cycle": 1,
    "timestamp": "2026-04-06T10:01:55Z",
    "verdict": "GO",
    "severity": "none",
    "confidence": 0.92,
    "agent_timings": {"lightman": 43, "vulcan": 34, "wopr": 12},
    "stats": {"go": 1, "caution": 0, "revert": 0, "errors": 0}
}
EOF

pause

# ── STEP 4: Deploy ─────────────────────────────────────────────────────
step "4/6" "Deploy — GO verdict triggers deployment"

echo ""
echo -e "  2026-04-06 10:02:00 INFO ${GREEN}Verdict GO${NC} — merging to main"
sleep 0.3
echo -e "  2026-04-06 10:02:01 INFO Running deploy: ${DIM}./deploy.sh${NC}"
sleep 0.5
echo -e "  2026-04-06 10:02:05 INFO ${GREEN}Deploy successful${NC}"
sleep 0.3
echo -e "  2026-04-06 10:02:06 INFO Health check: ${GREEN}200 OK${NC}"
echo ""
ok "Cycle 1 complete — changes deployed to production"

echo ""
echo -e "  ${DIM}────────── Now simulating a REVERT cycle ──────────${NC}"

pause

echo ""
echo -e "  2026-04-06 10:07:01 INFO Cycle ${BOLD}2${NC}/3"
sleep 0.3
echo -e "  2026-04-06 10:07:02 INFO ${CYAN}[lightman]${NC} Running: Remove hardcoded SECRET_KEY"
sleep 0.3
echo -e "  2026-04-06 10:07:40 INFO ${CYAN}[lightman]${NC} Done (38s)"
sleep 0.3
echo -e "  2026-04-06 10:07:41 INFO ${YELLOW}[wopr]${NC}     Gate review starting..."
sleep 0.5

VERDICT_REVERT='{
    "verdict": "REVERT",
    "severity": "high",
    "findings": "SECRET_KEY removal broke the authentication middleware. All /admin routes return 500. The env var fallback was not wired correctly — os.environ.get() returns None and the app crashes on startup.",
    "issues": [
        "app.py:12 — os.environ.get(\"SECRET_KEY\") has no default, crashes if unset",
        "auth.py:5 — middleware references SECRET_KEY before initialization",
        "No tests added for the env var path"
    ],
    "recommended_action": "Add a default value or raise a clear error at startup. Add tests for missing env var scenario.",
    "confidence": 0.95
}'

echo ""
echo -e "  ${YELLOW}WOPR verdict:${NC}"
echo ""
echo "$VERDICT_REVERT" | python3 -m json.tool 2>/dev/null | while IFS= read -r line; do
    echo -e "  ${RED}$line${NC}"
done

echo ""
fail "Verdict: ${RED}REVERT${NC} — severity HIGH, confidence 0.95"
warn "Rolling back changes via git revert"

echo ""
echo -e "  2026-04-06 10:08:10 INFO ${RED}REVERT${NC} — changes rolled back"
echo -e "  2026-04-06 10:08:11 INFO Gate blocked: dev agents paused until issue resolved"
echo -e "  2026-04-06 10:08:12 INFO ${YELLOW}Notification sent${NC} → Telegram"

# Save event
cat > "$EVENTS_DIR/cycle_0002.json" << EOF
{
    "cycle": 2,
    "timestamp": "2026-04-06T10:08:10Z",
    "verdict": "REVERT",
    "severity": "high",
    "confidence": 0.95,
    "agent_timings": {"lightman": 38, "wopr": 15},
    "issues": [
        "app.py:12 — os.environ.get(SECRET_KEY) has no default",
        "auth.py:5 — middleware references SECRET_KEY before init",
        "No tests for missing env var"
    ],
    "stats": {"go": 1, "caution": 0, "revert": 1, "errors": 0}
}
EOF

pause

# ── STEP 5: Logs ───────────────────────────────────────────────────────
step "5/6" "Logs — Full sprint log"

# Generate log
cat > "$LOGS_DIR/sprint.log" << 'LOGEOF'
2026-04-06 10:00:01 INFO Sprint started: demo-api (3 cycles, 3 agents)
2026-04-06 10:00:01 INFO Cycle 1/3
2026-04-06 10:00:02 INFO [lightman] task: Fix SQL injection in app.py
2026-04-06 10:00:45 INFO [lightman] done (43s) — 3 files changed
2026-04-06 10:00:46 INFO [vulcan] task: Find missing input validation
2026-04-06 10:01:20 INFO [vulcan] done (34s) — 2 issues fixed
2026-04-06 10:01:21 INFO [wopr] gate review
2026-04-06 10:01:55 INFO [wopr] verdict: GO (confidence: 0.92)
2026-04-06 10:02:00 INFO Merging cycle-1 → main
2026-04-06 10:02:01 INFO Deploy: ./deploy.sh
2026-04-06 10:02:05 INFO Deploy OK
2026-04-06 10:02:06 INFO Health check: 200 OK
2026-04-06 10:02:06 INFO Checkpoint saved: cycle=1 stats={go:1}
2026-04-06 10:07:01 INFO Cycle 2/3
2026-04-06 10:07:02 INFO [lightman] task: Remove hardcoded SECRET_KEY
2026-04-06 10:07:40 INFO [lightman] done (38s) — 2 files changed
2026-04-06 10:07:41 INFO [wopr] gate review
2026-04-06 10:08:10 INFO [wopr] verdict: REVERT (severity: high, confidence: 0.95)
2026-04-06 10:08:10 WARN REVERT — rolling back cycle-2 branch
2026-04-06 10:08:11 INFO Gate blocked: dev agents paused
2026-04-06 10:08:12 INFO Notification → Telegram: REVERT on demo-api cycle 2
2026-04-06 10:08:12 INFO Checkpoint saved: cycle=2 stats={go:1, revert:1}
LOGEOF

echo ""
echo -e "  ${DIM}\$ joshua status .joshua${NC}"
echo ""
cat "$LOGS_DIR/sprint.log" | while IFS= read -r line; do
    if echo "$line" | grep -q "REVERT"; then
        echo -e "  ${RED}$line${NC}"
    elif echo "$line" | grep -q "GO\|Deploy OK\|Health check"; then
        echo -e "  ${GREEN}$line${NC}"
    elif echo "$line" | grep -q "WARN"; then
        echo -e "  ${YELLOW}$line${NC}"
    else
        echo -e "  ${DIM}$line${NC}"
    fi
done

pause

# ── STEP 6: History ────────────────────────────────────────────────────
step "6/6" "History — Sprint checkpoint + event timeline"

# Save checkpoint
cat > "$STATE_DIR/checkpoint.json" << EOF
{
  "cycle": 2,
  "stats": {"go": 1, "caution": 0, "revert": 1, "errors": 0},
  "timestamp": "2026-04-06T10:08:12",
  "project": "demo-api",
  "gate_blocked": true,
  "last_gate_findings": "SECRET_KEY removal broke auth middleware",
  "consecutive_errors": 0
}
EOF

echo ""
echo -e "  ${BOLD}Checkpoint${NC} (.joshua/checkpoint.json):"
echo ""
python3 -m json.tool "$STATE_DIR/checkpoint.json" | while IFS= read -r line; do
    echo -e "  $line"
done

echo ""
echo -e "  ${BOLD}Event Timeline${NC} (.joshua/events/):"
echo ""
echo -e "  ${DIM}Cycle${NC}  ${DIM}Verdict${NC}    ${DIM}Severity${NC}  ${DIM}Conf${NC}   ${DIM}Agents${NC}          ${DIM}Time${NC}"
echo -e "  ${DIM}─────  ───────    ────────  ────   ──────          ────${NC}"
echo -e "  1      ${GREEN}GO${NC}         none      0.92   L:43s V:34s W:12s  89s"
echo -e "  2      ${RED}REVERT${NC}     high      0.95   L:38s W:15s        53s"

echo ""
echo -e "  ${BOLD}Totals:${NC} 2 cycles | ${GREEN}1 GO${NC} | ${RED}1 REVERT${NC} | 0 errors | 142s runtime"

pause

# ── HTTP Server API ────────────────────────────────────────────────────
banner
echo ""
echo -e "  ${BOLD}Bonus: HTTP Server API (v0.6.0)${NC}"
echo ""
echo -e "  ${DIM}Process-based runtime — SQLite single source of truth${NC}"
echo ""
echo -e "  ${CYAN}POST /sprints${NC}           Start a sprint (isolated worker process)"
echo -e "  ${CYAN}GET  /sprints${NC}           List all sprints"
echo -e "  ${CYAN}GET  /sprints/{id}${NC}      Sprint status (live PID + heartbeat)"
echo -e "  ${CYAN}POST /sprints/{id}/stop${NC} Graceful stop via SIGTERM"
echo -e "  ${CYAN}GET  /sprints/{id}/logs${NC} Tail sprint logs"
echo -e "  ${CYAN}GET  /health${NC}            Server health + runtime info"
echo ""
echo -e "  ${DIM}Features: SSRF protection, timing-safe auth, heartbeat liveness,${NC}"
echo -e "  ${DIM}supervisor auto-restart, WAL-mode SQLite, crash recovery${NC}"

banner
echo ""
echo -e "  ${BOLD}joshua-agent v0.6.0${NC} — ${DIM}pip install joshua-agent${NC}"
echo -e "  ${DIM}https://github.com/jorgevazquez-vagojo/joshua-agent${NC}"
echo ""
echo -e "  ${BOLD}\"The only winning move is to keep playing.\"${NC}"
echo ""

# Cleanup happens via trap
