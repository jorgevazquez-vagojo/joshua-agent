#!/usr/bin/env bash
# deploy.sh — Pull latest joshua-agent and restart any running joshua containers
#
# Usage:
#   bash deploy.sh                           # pull latest code
#   COMPOSE_DIR=/path/to/app bash deploy.sh  # also restart joshua container
set -euo pipefail

SPRINT_DIR="$(cd "$(dirname "$0")" && pwd)"
COMPOSE_DIR="${COMPOSE_DIR:-}"

echo "=== joshua-agent deploy ==="

# 1. Pull latest code
cd "$SPRINT_DIR"
git pull origin main
echo "✓ Code updated to $(git rev-parse --short HEAD)"

# 2. Restart joshua container if a compose project is specified
if [ -n "$COMPOSE_DIR" ] && [ -f "$COMPOSE_DIR/docker-compose.yml" ]; then
    if cd "$COMPOSE_DIR" && docker compose ps joshua --quiet 2>/dev/null | grep -q .; then
        docker compose restart joshua
        echo "✓ joshua container restarted"
    else
        echo "  joshua container not running in $COMPOSE_DIR — skipped"
    fi
else
    echo "  Set COMPOSE_DIR=/path/to/project to restart a running joshua container"
fi

# 3. System editable install is already live (no reinstall needed)
echo "✓ System install live (editable mode)"
echo "=== Deploy done ==="
