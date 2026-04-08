"""joshua-agent: A strange game. The only winning move is to play. Autonomous multi-agent sprints that learn."""

try:
    from importlib.metadata import version as _version
    __version__ = _version("joshua-agent")
except Exception:
    __version__ = "1.3.0"
