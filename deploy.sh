#!/usr/bin/env bash
# deploy.sh — Pull latest joshua-agent and restart running instances
set -euo pipefail

SPRINT_DIR="/home/jorge/projects/sprint-agents"
BRAIN_DIR="/home/jorge/projects/redegal-brain"

echo "=== joshua-agent deploy ==="

# 1. Pull latest code
cd "$SPRINT_DIR"
git pull origin main
echo "✓ Code updated to $(git rev-parse --short HEAD)"

# 2. Restart brain's joshua container if running
if cd "$BRAIN_DIR" && docker compose ps joshua --quiet 2>/dev/null | grep -q .; then
    docker compose restart joshua
    echo "✓ joshua container restarted"
else
    echo "  joshua container not running — skipped"
fi

# 3. System editable install is already live (no reinstall needed)
echo "✓ System install live (editable mode)"
echo "=== Deploy done ==="
