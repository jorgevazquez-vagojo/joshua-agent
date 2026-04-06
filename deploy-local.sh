#!/usr/bin/env bash
# deploy-local.sh — Pull latest joshua-agent locally and update venv
set -euo pipefail

SPRINT_DIR="$HOME/sprint-agents"

echo "=== joshua-agent local update ==="
cd "$SPRINT_DIR"
git pull origin main
echo "✓ Code updated to $(git rev-parse --short HEAD)"

# Reinstall in case dependencies changed
.venv/bin/pip install -e '.[server,dev]' --quiet
echo "✓ Local venv updated"
echo "=== Done ==="
