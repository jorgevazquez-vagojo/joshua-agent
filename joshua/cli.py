"""CLI entry point for joshua-agent."""

import signal
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import click

from joshua import __version__


@click.group()
@click.version_option(__version__)
def main():
    """joshua-agent: Shall we play a game?

    Autonomous multi-agent sprints that learn. Multiple AI agents with different
    skills work in continuous cycles. Works with Claude, Codex, Aider, or any LLM tool.
    """
    pass


@main.command()
@click.argument("config", type=click.Path(exists=True))
@click.option("--max-cycles", "-n", default=5, help="Max cycles (default: 5; 0 = infinite — use with caution)")
@click.option("--max-hours", "-H", default=2.0, help="Max hours budget (default: 2.0; 0 = no limit)")
@click.option("--dry-run", is_flag=True, help="Parse config and exit without running")
@click.option("--no-deploy", is_flag=True, help="Skip deploy_command even on GO verdict")
def run(config: str, max_cycles: int, max_hours: float, dry_run: bool, no_deploy: bool):
    """Run an autonomous sprint from a YAML config file.

    Example: joshua run my-project.yaml
    """
    from joshua.config import load_config
    from joshua.sprint import Sprint

    cfg = load_config(config)

    if max_cycles:
        cfg.setdefault("sprint", {})["max_cycles"] = max_cycles
    if max_hours:
        cfg.setdefault("sprint", {})["max_hours"] = max_hours
    if no_deploy:
        cfg.setdefault("sprint", {})["no_deploy"] = True

    if dry_run:
        click.echo(f"Config loaded OK: {cfg['project']['name']}")
        click.echo(f"  Runner: {cfg['runner']['type']}")
        agents = cfg.get("agents", {})
        click.echo(f"  Agents: {list(agents.keys())}")
        click.echo(f"  Path: {cfg['project']['path']}")
        return

    import logging
    log = logging.getLogger("joshua")
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)
    log.addHandler(stream_handler)

    # Rotating log file in .joshua/logs/
    state_dir = Path(cfg.get("memory", {}).get(
        "state_dir",
        Path(cfg["project"]["path"]).expanduser() / ".joshua"
    ))
    log_dir = state_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_dir / "sprint.log",
        maxBytes=100 * 1024 * 1024,  # 100MB
        backupCount=5,
    )
    file_handler.setFormatter(fmt)
    log.addHandler(file_handler)

    sprint = Sprint(cfg)

    # Graceful shutdown on SIGTERM (e.g., docker stop) and SIGINT (Ctrl+C)
    def _shutdown(signum, frame):
        log.info(f"Signal {signum} received — stopping sprint gracefully")
        sprint.stop()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    sprint.run()


@main.command()
@click.argument("state_dir", type=click.Path(), default=".joshua")
def status(state_dir: str):
    """Show sprint status dashboard.

    Example: joshua status .joshua
    """
    from joshua.utils.status import get_status, format_status

    state_path = Path(state_dir).expanduser().resolve()
    if not state_path.exists():
        click.echo(f"State directory not found: {state_path}")
        click.echo("Run a sprint first, or specify the correct path.")
        sys.exit(1)

    st = get_status(state_path)
    click.echo(format_status(st))


@main.command()
@click.argument("config", type=click.Path(exists=True))
def evolve(config: str):
    """Run agent evolution + wiki synthesis (normally daily via cron).

    Example: joshua evolve my-project.yaml
    """
    import logging
    from joshua.config import load_config
    from joshua.runners import runner_factory
    from joshua.agents import agents_from_config
    from joshua.memory.evolve import evolve_agent, synthesize_wiki, lint_wiki

    log = logging.getLogger("joshua")
    log.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)

    cfg = load_config(config)
    runner = runner_factory(cfg)
    agents = agents_from_config(cfg)
    state_dir = Path(cfg["project"]["path"]) / ".joshua"
    wiki_dir = str(state_dir / "wiki")
    project = cfg["project"]["name"]

    click.echo("=== Agent Evolution ===")
    for agent in agents:
        ok = evolve_agent(agent.name, state_dir, runner)
        status = "OK" if ok else "skipped"
        click.echo(f"  {agent.name}: {status}")

    click.echo("\n=== Wiki Synthesis ===")
    n = synthesize_wiki(project, state_dir, runner, wiki_dir)
    click.echo(f"  {n} entries synthesized")

    click.echo("\n=== Wiki Lint ===")
    report = lint_wiki(state_dir, runner, wiki_dir)
    click.echo(f"  {report[:300]}")


@main.command()
@click.option("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1 — loopback only; use 0.0.0.0 to expose on all interfaces)")
@click.option("--port", "-p", default=8100, help="Port")
def serve(host: str, port: int):
    """Start the Joshua HTTP server for programmatic sprint management.

    Example: joshua serve --port 8100
    """
    try:
        import uvicorn
    except ImportError:
        click.echo("Install server extras: pip install joshua-agent[server]")
        sys.exit(1)

    import logging
    log = logging.getLogger("joshua")
    log.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)

    click.echo(f"Joshua server starting on {host}:{port}")
    uvicorn.run("joshua.server:app", host=host, port=port, log_level="info")


@main.command()
@click.option("--output", "-o", default="", help="Output YAML file path (default: <project-slug>.yaml)")
def init(output: str):
    """Interactive setup wizard — generate a joshua config for your project.

    Example: joshua init
    """
    import re

    click.echo("")
    click.echo("  joshua-agent setup wizard")
    click.echo("  ─────────────────────────")
    click.echo("  Answer a few questions to generate your config file.")
    click.echo("")

    # ── Project ────────────────────────────────────────────────────────
    project_name = click.prompt("Project name", default="my-project")
    project_path = click.prompt(
        "Project path (absolute or relative to current dir)",
        default=".",
    )
    project_path = str(Path(project_path).expanduser().resolve())

    site_url = click.prompt("Site URL for live tests (leave blank to skip)", default="")

    # ── Runner ─────────────────────────────────────────────────────────
    click.echo("")
    click.echo("Runner options:")
    click.echo("  1. claude  — Claude Code CLI (recommended)")
    click.echo("  2. aider   — Aider")
    click.echo("  3. codex   — OpenAI Codex CLI")
    click.echo("  4. custom  — any CLI tool")
    runner_choice = click.prompt("Runner", type=click.Choice(["1", "2", "3", "4"]), default="1")
    runner_map = {"1": "claude", "2": "aider", "3": "codex", "4": "custom"}
    runner_type = runner_map[runner_choice]

    runner_model = ""
    if runner_type == "claude":
        runner_model = click.prompt(
            "Model (leave blank for default claude-sonnet-4-5)",
            default="",
        )

    custom_command = ""
    if runner_type == "custom":
        custom_command = click.prompt("Command template for custom runner")

    timeout = click.prompt("Runner timeout in seconds", default=1800, type=int)

    # ── Agents ─────────────────────────────────────────────────────────
    click.echo("")
    click.echo("Agents — every project needs at least one work agent and one gate agent.")
    click.echo("Common skills: developer, bug-hunter, researcher, performance, security, gate")
    click.echo("")

    agents: dict = {}

    # Work agent
    work_name = click.prompt("Work agent name", default="dev")
    work_skill = click.prompt(f"Skill for '{work_name}'", default="developer")
    work_task = click.prompt(
        f"Default task for '{work_name}'",
        default="Identify and fix bugs, improve code quality, and implement small enhancements.",
    )
    agents[work_name] = {"skill": work_skill, "instructions": work_task}

    add_second = click.confirm("Add a second work agent?", default=False)
    if add_second:
        w2_name = click.prompt("Second work agent name", default="qa")
        w2_skill = click.prompt(f"Skill for '{w2_name}'", default="bug-hunter")
        w2_task = click.prompt(
            f"Default task for '{w2_name}'",
            default="Find bugs, regressions, and edge cases. Write failing tests.",
        )
        agents[w2_name] = {"skill": w2_skill, "instructions": w2_task}

    # Gate agent
    gate_name = click.prompt("Gate agent name", default="gate")
    gate_skill = click.prompt(f"Skill for '{gate_name}'", default="gate")
    agents[gate_name] = {
        "skill": gate_skill,
        "instructions": (
            "Review the changes made this cycle. "
            "Return a JSON block with verdict (GO/CAUTION/REVERT), "
            "severity, findings, and confidence."
        ),
    }

    # ── Sprint settings ────────────────────────────────────────────────
    click.echo("")
    max_cycles = click.prompt("Max cycles per run (0 = infinite)", default=10, type=int)
    max_hours = click.prompt("Max hours per run (0 = no limit)", default=2.0, type=float)
    cycle_sleep = click.prompt("Seconds to sleep between cycles", default=60, type=int)

    git_strategy = click.prompt(
        "Git strategy",
        type=click.Choice(["none", "snapshot", "hillclimb"]),
        default="snapshot",
    )

    deploy_cmd = click.prompt(
        "Deploy command on GO verdict (leave blank to skip)",
        default="",
    )
    health_url = click.prompt(
        "Health-check URL after deploy (leave blank to skip)",
        default="",
    )

    # ── Notifications ─────────────────────────────────────────────────
    click.echo("")
    notif_choice = click.prompt(
        "Notifications",
        type=click.Choice(["none", "telegram", "slack", "discord", "webhook"]),
        default="none",
    )
    notif_config: dict = {"type": notif_choice}
    if notif_choice == "telegram":
        notif_config["token"] = click.prompt("Telegram bot token")
        notif_config["chat_id"] = click.prompt("Telegram chat_id")
    elif notif_choice == "slack":
        notif_config["webhook_url"] = click.prompt("Slack webhook URL")
    elif notif_choice == "discord":
        notif_config["webhook_url"] = click.prompt("Discord webhook URL")
    elif notif_choice == "webhook":
        notif_config["url"] = click.prompt("Webhook URL")

    # ── Memory ────────────────────────────────────────────────────────
    memory_enabled = click.confirm("Enable self-learning memory?", default=True)

    # ── Assemble YAML ─────────────────────────────────────────────────
    slug = re.sub(r"[^a-z0-9]+", "-", project_name.lower()).strip("-")
    out_path = output or f"{slug}.yaml"

    lines = [
        f"# joshua-agent config — generated by `joshua init`",
        f"",
        f"project:",
        f"  name: {project_name}",
        f"  path: {project_path}",
    ]
    if deploy_cmd:
        lines.append(f"  deploy: {deploy_cmd}")
    if health_url:
        lines.append(f"  health_url: {health_url}")
    if site_url:
        lines.append(f"  site_url: {site_url}")

    lines += ["", "runner:", f"  type: {runner_type}"]
    if runner_model:
        lines.append(f"  model: {runner_model}")
    if custom_command:
        lines.append(f"  command: {custom_command}")
    lines.append(f"  timeout: {timeout}")

    lines += ["", "agents:"]
    for aname, acfg in agents.items():
        lines.append(f"  {aname}:")
        lines.append(f"    skill: {acfg['skill']}")
        # Wrap instructions as literal block scalar if multiline-ish
        instr = acfg.get("instructions", "")
        if instr:
            lines.append(f"    instructions: >-")
            lines.append(f"      {instr}")

    lines += [
        "",
        "sprint:",
        f"  max_cycles: {max_cycles}",
        f"  max_hours: {max_hours}",
        f"  cycle_sleep: {cycle_sleep}",
        f"  git_strategy: {git_strategy}",
    ]
    if health_url:
        lines.append("  health_check: true")

    lines += [
        "",
        "memory:",
        f"  enabled: {'true' if memory_enabled else 'false'}",
    ]

    if notif_choice != "none":
        lines += ["", "notifications:"]
        for k, v in notif_config.items():
            lines.append(f"  {k}: {v}")

    yaml_content = "\n".join(lines) + "\n"

    Path(out_path).write_text(yaml_content)

    click.echo("")
    click.echo(f"  Config written to: {out_path}")
    click.echo("")
    click.echo("  Next steps:")
    click.echo(f"    joshua run {out_path} --max-cycles 1 --dry-run   # validate")
    click.echo(f"    joshua run {out_path}                             # start sprint")
    click.echo("")


if __name__ == "__main__":
    main()
