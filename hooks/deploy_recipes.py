#!/usr/bin/env python3
"""
deploy_recipes.py — Pre-defined deploy recipes for joshua-agent.

Instead of raw shell commands in YAML, recipes are named patterns
with pre/post steps, rollback, health checks, and environment-specific variants.

Usage in YAML config:
    project:
      deploy_recipe: docker-compose-rebuild   # use a named recipe
      # or inline:
      deploy_recipe:
        type: docker-compose
        service: redegal-mecano
        no_cache: true

Usage as CLI:
    python3 deploy_recipes.py run docker-compose-rebuild --project-dir ~/projects/redegal-mecano
    python3 deploy_recipes.py list
    python3 deploy_recipes.py validate ~/prefect-agents/redegal-mecano-dev.yaml

Built-in recipes:
  docker-compose-rebuild   Full rebuild + recreate (production-safe)
  docker-compose-restart   Restart existing containers (fast, no rebuild)
  rsync-docker             rsync to server + docker compose up
  npm-build-deploy         npm build + sync dist/
  python-reload            Touch wsgi/asgi trigger file (Gunicorn reload)
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


BUILT_IN_RECIPES: dict[str, dict] = {
    "docker-compose-rebuild": {
        "description": "Full Docker build (no-cache) and recreate — production-safe",
        "steps": [
            "docker compose build --no-cache",
            "docker compose up -d --force-recreate",
        ],
        "health_check_delay": 15,
        "rollback": "docker compose down && docker compose up -d",
    },
    "docker-compose-restart": {
        "description": "Restart containers without rebuild — fast, for config changes",
        "steps": [
            "docker compose restart",
        ],
        "health_check_delay": 5,
        "rollback": "docker compose restart",
    },
    "docker-compose-service": {
        "description": "Rebuild and restart a single service",
        "steps": [
            "docker compose build --no-cache {service}",
            "docker compose up -d --no-deps --force-recreate {service}",
        ],
        "health_check_delay": 10,
    },
    "rsync-docker": {
        "description": "Sync code to remote server then rebuild Docker",
        "steps": [
            "rsync -avz --exclude='.git' --exclude='node_modules' . {remote_user}@{remote_host}:{remote_path}",
            "ssh {remote_user}@{remote_host} 'cd {remote_path} && docker compose build --no-cache && docker compose up -d --force-recreate'",
        ],
        "health_check_delay": 20,
    },
    "npm-build": {
        "description": "Build frontend assets",
        "steps": [
            "npm ci",
            "npm run build",
        ],
        "health_check_delay": 0,
    },
    "python-reload": {
        "description": "Reload Python WSGI/ASGI server (touch reload file)",
        "steps": [
            "touch {reload_file}",
        ],
        "health_check_delay": 3,
    },
}


def interpolate(template: str, params: dict) -> str:
    """Replace {key} placeholders with params."""
    result = template
    for key, val in params.items():
        result = result.replace(f"{{{key}}}", str(val))
    return result


def run_step(cmd: str, cwd: str, timeout: int = 300) -> tuple[bool, str]:
    try:
        r = subprocess.run(
            cmd, shell=False, cwd=cwd,
            args=cmd.split(), capture_output=True, text=True, timeout=timeout
        )
        return r.returncode == 0, r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return False, f"Timeout after {timeout}s"
    except Exception as e:
        return False, str(e)


def run_recipe(recipe_name: str, project_dir: str, params: dict,
               dry_run: bool = False) -> tuple[bool, str]:
    """Execute a named recipe. Returns (success, output)."""
    if recipe_name not in BUILT_IN_RECIPES:
        return False, f"Unknown recipe: {recipe_name}. Available: {list(BUILT_IN_RECIPES)}"

    recipe = BUILT_IN_RECIPES[recipe_name]
    output_lines = [f"Recipe: {recipe_name} — {recipe['description']}"]

    for step_template in recipe["steps"]:
        cmd = interpolate(step_template, params)
        output_lines.append(f"  → {cmd}")

        if dry_run:
            output_lines.append("    [DRY RUN]")
            continue

        ok, out = run_step(cmd, project_dir)
        output_lines.append(f"    {'✓' if ok else '✗'} {out[:200]}")
        if not ok:
            output_lines.append(f"  FAILED at step: {cmd}")
            return False, "\n".join(output_lines)

    return True, "\n".join(output_lines)


def list_recipes() -> str:
    lines = ["Available deploy recipes:\n"]
    for name, recipe in BUILT_IN_RECIPES.items():
        lines.append(f"  {name:<30} {recipe['description']}")
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(description="Joshua deploy recipes")
    sub = p.add_subparsers(dest="cmd")

    run_p = sub.add_parser("run", help="Execute a recipe")
    run_p.add_argument("recipe",        help="Recipe name")
    run_p.add_argument("--project-dir", default=".", help="Working directory")
    run_p.add_argument("--service",     default="", help="Docker service name (for service recipe)")
    run_p.add_argument("--dry-run",     action="store_true")
    run_p.add_argument("--param",       action="append", default=[],
                       help="Extra params as key=value (repeatable)")

    list_p = sub.add_parser("list", help="List available recipes")

    args = p.parse_args()

    if args.cmd == "list" or not args.cmd:
        print(list_recipes())
        return

    if args.cmd == "run":
        params = {"service": args.service}
        for kv in args.param:
            k, _, v = kv.partition("=")
            params[k] = v

        ok, output = run_recipe(
            args.recipe,
            str(Path(args.project_dir).expanduser().resolve()),
            params,
            dry_run=args.dry_run,
        )
        print(output)
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
