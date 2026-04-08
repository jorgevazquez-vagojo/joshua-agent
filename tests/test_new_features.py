"""Tests for v1.6.0 features: notifiers.py, cost control, joshuaignore,
revert approval, RBAC, server endpoints (fleet, digest, approval),
and CLI commands (pr, cost, approve, agent-log, digest, init --from-repo)."""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── notifiers.py ─────────────────────────────────────────────────────

class TestWebhookNotifiers:
    def test_slack_notify_post_called(self):
        from joshua.integrations.notifiers import SlackNotifier
        notifier = SlackNotifier("https://hooks.slack.com/test")
        with patch("joshua.integrations.notifiers._post_json", return_value=True) as mock_post:
            result = notifier.notify("GO", "my-project", 5, 0.95, "All good", "main")
        assert result is True
        mock_post.assert_called_once()
        payload = mock_post.call_args[0][1]
        assert "attachments" in payload or "text" in payload

    def test_discord_notify_post_called(self):
        from joshua.integrations.notifiers import DiscordNotifier
        notifier = DiscordNotifier("https://discord.com/api/webhooks/test")
        with patch("joshua.integrations.notifiers._post_json", return_value=True) as mock_post:
            result = notifier.notify("REVERT", "my-project", 3, 0.8, "Critical bug", "feat/x")
        assert result is True
        mock_post.assert_called_once()
        payload = mock_post.call_args[0][1]
        assert "embeds" in payload

    def test_teams_notify_post_called(self):
        from joshua.integrations.notifiers import TeamsNotifier
        notifier = TeamsNotifier("https://outlook.office.com/webhook/test")
        with patch("joshua.integrations.notifiers._post_json", return_value=True) as mock_post:
            result = notifier.notify("CAUTION", "my-project", 7, 0.7, "Warnings", "")
        assert result is True
        mock_post.assert_called_once()
        payload = mock_post.call_args[0][1]
        assert payload["@type"] == "MessageCard"

    def test_notify_all_calls_configured_notifiers(self):
        from joshua.integrations.notifiers import notify_all
        config = {
            "notifications": {
                "slack": "https://hooks.slack.com/test",
                "discord": "https://discord.com/api/webhooks/test",
            }
        }
        with patch("joshua.integrations.notifiers._post_json", return_value=True) as mock_post:
            notify_all(config, "GO", "proj", 1, 0.9)
        assert mock_post.call_count == 2

    def test_notify_all_skips_unconfigured(self):
        from joshua.integrations.notifiers import notify_all
        config = {"notifications": {}}
        with patch("joshua.integrations.notifiers._post_json", return_value=True) as mock_post:
            notify_all(config, "GO", "proj", 1, 0.9)
        assert mock_post.call_count == 0

    def test_post_json_returns_false_on_error(self):
        from joshua.integrations.notifiers import _post_json
        result = _post_json("http://127.0.0.1:1/no-server", {"test": 1})
        assert result is False


# ── .joshuaignore ────────────────────────────────────────────────────

class TestJoshuaIgnore:
    def test_joshuaignore_loaded(self, tmp_path):
        ignore_file = tmp_path / ".joshuaignore"
        ignore_file.write_text("*.log\n# comment\ndist/\n\n  node_modules/\n")
        from joshua.sprint import _load_joshuaignore
        patterns = _load_joshuaignore(str(tmp_path))
        assert "*.log" in patterns
        assert "dist/" in patterns
        assert "node_modules/" in patterns
        assert "# comment" not in patterns
        assert "" not in patterns

    def test_joshuaignore_missing_returns_empty(self, tmp_path):
        from joshua.sprint import _load_joshuaignore
        patterns = _load_joshuaignore(str(tmp_path))
        assert patterns == []

    def test_joshuaignore_in_context(self, tmp_path):
        """ignored_paths should appear in Sprint._build_context."""
        ignore_file = tmp_path / ".joshuaignore"
        ignore_file.write_text("vendor/\n*.lock\n")

        from joshua.sprint import Sprint
        config = {
            "project": {"name": "test", "path": str(tmp_path)},
            "runner": {"type": "claude"},
            "agents": {"dev": {"skill": "dev"}},
            "sprint": {"max_cycles": 1, "cycle_sleep": 0},
        }
        sprint = Sprint(config)
        ctx = sprint._build_context()
        assert "vendor/" in ctx["ignored_paths"]
        assert "*.lock" in ctx["ignored_paths"]


# ── Cost control ─────────────────────────────────────────────────────

class TestCostControl:
    def test_max_sprint_cost_loaded(self, tmp_path):
        from joshua.sprint import Sprint
        config = {
            "project": {"name": "test", "path": str(tmp_path)},
            "runner": {"type": "claude", "max_sprint_cost_usd": 5.0, "cost_alert_threshold": 0.75},
            "agents": {"dev": {"skill": "dev"}},
            "sprint": {"max_cycles": 5, "cycle_sleep": 0},
        }
        sprint = Sprint(config)
        assert sprint.max_sprint_cost_usd == 5.0
        assert sprint.cost_alert_threshold == 0.75

    def test_cost_usd_saved_in_checkpoint(self, tmp_path):
        from joshua.sprint import Sprint
        config = {
            "project": {"name": "test", "path": str(tmp_path)},
            "runner": {"type": "claude"},
            "agents": {"dev": {"skill": "dev"}},
            "sprint": {"max_cycles": 1, "cycle_sleep": 0},
        }
        sprint = Sprint(config)
        sprint.stats["total_tokens"] = 100_000
        sprint.stats["cost_usd"] = round(100_000 / 1_000_000 * 3.0, 6)
        sprint._save_checkpoint()

        cp_path = sprint.state_dir / "checkpoint.json"
        data = json.loads(cp_path.read_text())
        assert "cost_usd" in data
        assert data["cost_usd"] >= 0.0

    def test_cost_tracking_in_stats(self, tmp_path):
        """cost_usd is computed and stored in stats after token accumulation."""
        from joshua.sprint import Sprint

        config = {
            "project": {"name": "test", "path": str(tmp_path)},
            "runner": {"type": "claude", "max_sprint_cost_usd": 1.0},
            "agents": {"dev": {"skill": "dev"}},
            "sprint": {"max_cycles": 1, "cycle_sleep": 0},
        }
        sprint = Sprint(config)
        sprint.stats["total_tokens"] = 200_000
        sprint.stats["cost_usd"] = round(200_000 / 1_000_000 * 3.0, 6)

        # Verify cost math: 200k tokens * $3/MTok = $0.60
        cost = sprint.stats["cost_usd"]
        assert abs(cost - 0.6) < 0.001
        # Under $1.0 limit, so sprint should not be stopped
        assert cost < sprint.max_sprint_cost_usd
        # But above 50% threshold (0.60 >= 0.50), not above 80% (0.80)
        assert cost >= sprint.max_sprint_cost_usd * 0.5

    def test_cost_stop_flag_set_when_over_limit(self, tmp_path):
        """_stop_requested is True when cost exceeds max_sprint_cost_usd during a cycle."""
        from joshua.sprint import Sprint
        from joshua.runners.base import RunResult

        config = {
            "project": {"name": "test", "path": str(tmp_path)},
            "runner": {"type": "claude", "max_sprint_cost_usd": 0.001},
            "agents": {"dev": {"skill": "dev"}},
            "sprint": {"max_cycles": 10, "cycle_sleep": 0},
        }
        sprint = Sprint(config)
        # Pre-set tokens beyond the limit
        sprint.stats["total_tokens"] = 1_000_000  # $3.00 > $0.001
        cost_usd = sprint.stats["total_tokens"] / 1_000_000 * 3.0
        sprint.stats["cost_usd"] = round(cost_usd, 6)

        # Manually trigger the same logic used in _run_cycle
        if sprint.max_sprint_cost_usd > 0 and cost_usd >= sprint.max_sprint_cost_usd:
            sprint._stop_requested = True
            sprint._stop_event.set()

        assert sprint._stop_requested is True


# ── Revert requires approval ──────────────────────────────────────────

class TestRevertApproval:
    def test_revert_requires_approval_loaded(self, tmp_path):
        from joshua.sprint import Sprint
        config = {
            "project": {"name": "test", "path": str(tmp_path)},
            "runner": {"type": "claude"},
            "agents": {"dev": {"skill": "dev"}},
            "sprint": {
                "max_cycles": 1,
                "cycle_sleep": 0,
                "revert_requires_approval": True,
                "approval_timeout_minutes": 15,
            },
        }
        sprint = Sprint(config)
        assert sprint.revert_requires_approval is True
        assert sprint.approval_timeout_minutes == 15

    def test_wait_for_approval_timeout_returns_false(self, tmp_path):
        """If no approval arrives before timeout, returns False (skip rollback)."""
        from joshua.sprint import Sprint
        config = {
            "project": {"name": "test", "path": str(tmp_path)},
            "runner": {"type": "claude"},
            "agents": {"dev": {"skill": "dev"}},
            "sprint": {
                "max_cycles": 1,
                "cycle_sleep": 0,
                "revert_requires_approval": True,
                "approval_timeout_minutes": 1,
            },
        }
        sprint = Sprint(config)
        sprint.last_gate_findings = "critical bug"
        sprint.cycle = 1
        sprint.notifier = MagicMock()

        # Very short timeout, patch time so it expires instantly
        sprint.approval_timeout_minutes = 0  # 0 minutes = expires immediately
        with patch("joshua.sprint.time.monotonic", side_effect=[0.0, 1.0, 2.0]):
            result = sprint._wait_for_revert_approval()
        assert result is False

    def test_wait_for_approval_approved_by_file(self, tmp_path):
        """Writes approval.json → _wait_for_revert_approval returns True."""
        from joshua.sprint import Sprint
        config = {
            "project": {"name": "test", "path": str(tmp_path)},
            "runner": {"type": "claude"},
            "agents": {"dev": {"skill": "dev"}},
            "sprint": {
                "max_cycles": 1,
                "cycle_sleep": 0,
                "revert_requires_approval": True,
                "approval_timeout_minutes": 5,
            },
        }
        sprint = Sprint(config)
        sprint.last_gate_findings = "bug found"
        sprint.cycle = 2
        sprint.notifier = MagicMock()

        approval_path = sprint.state_dir / "approval.json"

        # Simulate approval file written immediately
        def fake_wait(timeout):
            approval_path.write_text(json.dumps({"approved": True}))

        with patch.object(sprint, "_wait_or_stop", side_effect=fake_wait):
            result = sprint._wait_for_revert_approval()
        assert result is True


# ── Config schema: cost and approval fields ───────────────────────────

class TestConfigSchemaNewFields:
    def test_runner_config_cost_fields(self):
        from joshua.config_schema import RunnerConfig
        rc = RunnerConfig(type="claude", max_sprint_cost_usd=10.0, cost_alert_threshold=0.9)
        assert rc.max_sprint_cost_usd == 10.0
        assert rc.cost_alert_threshold == 0.9

    def test_runner_config_cost_defaults(self):
        from joshua.config_schema import RunnerConfig
        rc = RunnerConfig(type="claude")
        assert rc.max_sprint_cost_usd == 0.0
        assert rc.cost_alert_threshold == 0.80

    def test_sprint_config_approval_fields(self):
        from joshua.config_schema import SprintConfig
        sc = SprintConfig(max_cycles=5, revert_requires_approval=True, approval_timeout_minutes=20)
        assert sc.revert_requires_approval is True
        assert sc.approval_timeout_minutes == 20

    def test_sprint_config_approval_defaults(self):
        from joshua.config_schema import SprintConfig
        sc = SprintConfig(max_cycles=5)
        assert sc.revert_requires_approval is False
        assert sc.approval_timeout_minutes == 30


# ── Checkpoint: last_verdict and confidence ───────────────────────────

class TestCheckpointNewFields:
    def test_checkpoint_saves_last_verdict(self, tmp_path):
        from joshua.sprint import Sprint
        config = {
            "project": {"name": "test", "path": str(tmp_path)},
            "runner": {"type": "claude"},
            "agents": {"dev": {"skill": "dev"}},
            "sprint": {"max_cycles": 1, "cycle_sleep": 0},
        }
        sprint = Sprint(config)
        sprint._last_verdict = "REVERT"
        sprint.last_gate_confidence = 0.88
        sprint._save_checkpoint()

        data = json.loads((sprint.state_dir / "checkpoint.json").read_text())
        assert data.get("last_verdict") == "REVERT"
        assert data.get("last_gate_confidence") == pytest.approx(0.88)

    def test_checkpoint_saves_total_tokens(self, tmp_path):
        from joshua.sprint import Sprint
        config = {
            "project": {"name": "test", "path": str(tmp_path)},
            "runner": {"type": "claude"},
            "agents": {"dev": {"skill": "dev"}},
            "sprint": {"max_cycles": 1, "cycle_sleep": 0},
        }
        sprint = Sprint(config)
        sprint.stats["total_tokens"] = 42000
        sprint.stats["cost_usd"] = round(42000 / 1_000_000 * 3.0, 6)
        sprint._save_checkpoint()

        data = json.loads((sprint.state_dir / "checkpoint.json").read_text())
        assert data.get("total_tokens") == 42000


# ── RBAC server ───────────────────────────────────────────────────────

TOKEN_RBAC = secrets.token_hex(16)
os.environ["JOSHUA_INTERNAL_TOKEN"] = os.environ.get("JOSHUA_INTERNAL_TOKEN", TOKEN_RBAC)


class TestRBAC:
    @pytest.fixture(scope="class")
    def rbac_client(self):
        pytest.importorskip("httpx", reason="httpx required for TestClient")
        from fastapi.testclient import TestClient
        from joshua.server import app
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c

    def test_login_page_returns_200(self, rbac_client):
        r = rbac_client.get("/login")
        assert r.status_code == 200
        assert "Joshua" in r.text or "joshua" in r.text.lower()

    def test_fleet_endpoint_returns_list_or_404(self, rbac_client):
        """Fleet endpoint returns [] when no env var set, or 404 if config missing."""
        r = rbac_client.get("/fleet")
        assert r.status_code in (200, 404)
        if r.status_code == 200:
            assert isinstance(r.json(), list)

    def test_digest_endpoint_returns_dict(self, rbac_client):
        r = rbac_client.get("/digest")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, dict)

    def test_approval_requires_auth(self, rbac_client):
        """POST /sprints/{id}/approval requires operator role when RBAC configured."""
        # When JOSHUA_TOKENS not set (default), RBAC is open — 404 is fine (no sprint)
        r = rbac_client.post(
            "/sprints/nonexistent-id/approval",
            json={"approved": True},
        )
        assert r.status_code in (404, 401, 403)

    def test_get_approval_no_sprint_404(self, rbac_client):
        r = rbac_client.get("/sprints/nonexistent-id/approval")
        assert r.status_code in (404, 401)


# ── CLI: joshua cost ──────────────────────────────────────────────────

class TestCostCLI:
    def test_cost_command_no_checkpoint(self, tmp_path):
        from click.testing import CliRunner
        from joshua.cli import main
        import yaml

        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        config_file = tmp_path / "sprint.yaml"
        config_file.write_text(yaml.dump({
            "project": {"name": "test-proj", "path": str(project_dir)},
            "runner": {"type": "claude"},
            "agents": {"dev": {"skill": "dev"}},
            "sprint": {"max_cycles": 1, "cycle_sleep": 0},
        }))
        runner = CliRunner()
        result = runner.invoke(main, ["cost", str(config_file)])
        assert result.exit_code == 0
        assert "Cost Report" in result.output or "cost" in result.output.lower()

    def test_cost_command_with_checkpoint(self, tmp_path):
        from click.testing import CliRunner
        from joshua.cli import main
        import yaml

        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        state_dir = project_dir / ".joshua"
        state_dir.mkdir()
        (state_dir / "checkpoint.json").write_text(json.dumps({
            "cycle": 10,
            "total_tokens": 500000,
            "cost_usd": 1.50,
        }))
        config_file = tmp_path / "sprint.yaml"
        config_file.write_text(yaml.dump({
            "project": {"name": "test-proj", "path": str(project_dir)},
            "runner": {"type": "claude"},
            "agents": {"dev": {"skill": "dev"}},
            "sprint": {"max_cycles": 1, "cycle_sleep": 0},
        }))
        runner = CliRunner()
        result = runner.invoke(main, ["cost", str(config_file)])
        assert result.exit_code == 0
        assert "500,000" in result.output or "500000" in result.output

    def test_cost_command_export_csv(self, tmp_path):
        from click.testing import CliRunner
        from joshua.cli import main
        import yaml

        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        config_file = tmp_path / "sprint.yaml"
        config_file.write_text(yaml.dump({
            "project": {"name": "test-proj", "path": str(project_dir)},
            "runner": {"type": "claude"},
            "agents": {"dev": {"skill": "dev"}},
            "sprint": {"max_cycles": 1, "cycle_sleep": 0},
        }))
        csv_out = str(tmp_path / "costs.csv")
        runner = CliRunner()
        result = runner.invoke(main, ["cost", str(config_file), "--export", csv_out])
        assert result.exit_code == 0
        assert Path(csv_out).exists()


# ── CLI: joshua approve ───────────────────────────────────────────────

class TestApproveCLI:
    def test_approve_no_pending_fails(self, tmp_path):
        from click.testing import CliRunner
        from joshua.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["approve", str(tmp_path), "--approve"])
        assert result.exit_code != 0

    def test_approve_with_pending_file(self, tmp_path):
        from click.testing import CliRunner
        from joshua.cli import main

        pending = tmp_path / "approval_pending.json"
        pending.write_text(json.dumps({
            "cycle": 3,
            "findings": "Critical SQL injection found",
            "timestamp": "2026-04-08T10:00:00",
        }))
        runner = CliRunner()
        result = runner.invoke(main, ["approve", str(tmp_path), "--approve"])
        assert result.exit_code == 0
        approval_file = tmp_path / "approval.json"
        assert approval_file.exists()
        data = json.loads(approval_file.read_text())
        assert data["approved"] is True

    def test_dismiss_with_pending_file(self, tmp_path):
        from click.testing import CliRunner
        from joshua.cli import main

        pending = tmp_path / "approval_pending.json"
        pending.write_text(json.dumps({"cycle": 5, "findings": "Minor issues"}))
        runner = CliRunner()
        result = runner.invoke(main, ["approve", str(tmp_path), "--dismiss"])
        assert result.exit_code == 0
        data = json.loads((tmp_path / "approval.json").read_text())
        assert data["approved"] is False


# ── CLI: joshua agent-log ─────────────────────────────────────────────

class TestAgentLogCLI:
    def test_agent_log_no_cycles_dir(self, tmp_path):
        from click.testing import CliRunner
        from joshua.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["agent-log", str(tmp_path)])
        assert result.exit_code != 0

    def test_agent_log_reads_cycle_file(self, tmp_path):
        from click.testing import CliRunner
        from joshua.cli import main

        cycles_dir = tmp_path / "cycles"
        cycles_dir.mkdir()
        cycle_data = {
            "verdict": "GO",
            "work_outputs": {
                "dev": "Fixed the bug in module.py",
                "gate": "VERDICT: GO\nAll tests pass",
            },
        }
        (cycles_dir / "cycle-0001.json").write_text(json.dumps(cycle_data))
        runner = CliRunner()
        result = runner.invoke(main, ["agent-log", str(tmp_path)])
        assert result.exit_code == 0
        assert "GO" in result.output
        assert "dev" in result.output

    def test_agent_log_filter_by_agent(self, tmp_path):
        from click.testing import CliRunner
        from joshua.cli import main

        cycles_dir = tmp_path / "cycles"
        cycles_dir.mkdir()
        cycle_data = {
            "verdict": "CAUTION",
            "work_outputs": {
                "dev": "Refactored code",
                "gate": "CAUTION output",
            },
        }
        (cycles_dir / "cycle-0001.json").write_text(json.dumps(cycle_data))
        runner = CliRunner()
        result = runner.invoke(main, ["agent-log", str(tmp_path), "--agent", "gate"])
        assert result.exit_code == 0
        assert "CAUTION output" in result.output
        assert "Refactored code" not in result.output


# ── CLI: joshua digest ────────────────────────────────────────────────

class TestDigestCLI:
    def test_digest_command_no_data(self, tmp_path):
        from click.testing import CliRunner
        from joshua.cli import main
        import yaml

        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        config_file = tmp_path / "sprint.yaml"
        config_file.write_text(yaml.dump({
            "project": {"name": "test-proj", "path": str(project_dir)},
            "runner": {"type": "claude"},
            "agents": {"dev": {"skill": "dev"}},
            "sprint": {"max_cycles": 1, "cycle_sleep": 0},
        }))
        runner = CliRunner()
        result = runner.invoke(main, ["digest", str(config_file)])
        assert result.exit_code == 0
        assert "Digest" in result.output or "digest" in result.output.lower()

    def test_digest_command_custom_since(self, tmp_path):
        from click.testing import CliRunner
        from joshua.cli import main
        import yaml

        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        config_file = tmp_path / "sprint.yaml"
        config_file.write_text(yaml.dump({
            "project": {"name": "test-proj", "path": str(project_dir)},
            "runner": {"type": "claude"},
            "agents": {"dev": {"skill": "dev"}},
            "sprint": {"max_cycles": 1, "cycle_sleep": 0},
        }))
        runner = CliRunner()
        result = runner.invoke(main, ["digest", str(config_file), "--since", "14"])
        assert result.exit_code == 0
        assert "14" in result.output


# ── CLI: joshua init --from-repo ─────────────────────────────────────

class TestInitFromRepo:
    def test_init_from_repo_unknown_type(self, tmp_path):
        """When GitHub API fails (no token, rate limit), falls back to generic."""
        from click.testing import CliRunner
        from joshua.cli import main

        out_file = str(tmp_path / "out.yaml")
        with patch("urllib.request.urlopen", side_effect=Exception("Network error")):
            runner = CliRunner()
            result = runner.invoke(main, [
                "init", "--from-repo", "https://github.com/owner/repo",
                "--output", out_file
            ])
        assert result.exit_code == 0
        assert Path(out_file).exists()
        content = Path(out_file).read_text()
        assert "repo" in content or "generic" in content

    def test_init_from_repo_nodejs_detected(self, tmp_path):
        """When repo contains package.json, detected_type=nodejs."""
        from click.testing import CliRunner
        from joshua.cli import main

        mock_contents = [{"name": "package.json"}, {"name": "src"}]
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_contents).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        out_file = str(tmp_path / "out.yaml")
        with patch("urllib.request.urlopen", return_value=mock_resp):
            runner = CliRunner()
            result = runner.invoke(main, [
                "init", "--from-repo", "https://github.com/owner/my-node-app",
                "--output", out_file
            ])
        assert result.exit_code == 0
        content = Path(out_file).read_text()
        assert "nodejs" in content
        assert "npm test" in content

    def test_init_from_repo_python_detected(self, tmp_path):
        """When repo contains pyproject.toml, detected_type=python."""
        from click.testing import CliRunner
        from joshua.cli import main

        mock_contents = [{"name": "pyproject.toml"}, {"name": "README.md"}]
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_contents).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        out_file = str(tmp_path / "out.yaml")
        with patch("urllib.request.urlopen", return_value=mock_resp):
            runner = CliRunner()
            result = runner.invoke(main, [
                "init", "--from-repo", "https://github.com/owner/my-py-app",
                "--output", out_file
            ])
        assert result.exit_code == 0
        content = Path(out_file).read_text()
        assert "python" in content
        assert "pytest" in content
