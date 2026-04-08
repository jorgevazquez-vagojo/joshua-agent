"""Tests for safe command execution helpers."""

from pathlib import Path

from joshua.utils.safe_cmd import run_command


def _write_script(path: Path, content: str = "echo ok\n") -> Path:
    path.write_text(content)
    return path


class TestSafeCommandPaths:
    def test_run_command_allows_allowed_path(self, tmp_path):
        allowed_dir = tmp_path / "allowed"
        allowed_dir.mkdir()
        script = _write_script(allowed_dir / "deploy.sh")

        success, output = run_command(
            f"bash {script}",
            cwd=str(allowed_dir),
            allowed_paths=[str(allowed_dir)],
        )

        assert success is True
        assert "ok" in output

    def test_run_command_blocks_path_outside_allowlist(self, tmp_path):
        allowed_dir = tmp_path / "allowed"
        blocked_dir = tmp_path / "blocked"
        allowed_dir.mkdir()
        blocked_dir.mkdir()
        script = _write_script(blocked_dir / "deploy.sh")

        success, output = run_command(
            f"bash {script}",
            cwd=str(allowed_dir),
            allowed_paths=[str(allowed_dir)],
        )

        assert success is False
        assert "not inside the allowed path list" in output
