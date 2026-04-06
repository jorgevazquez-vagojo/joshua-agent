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
def run(config: str, max_cycles: int, max_hours: float, dry_run: bool):
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


if __name__ == "__main__":
    main()
