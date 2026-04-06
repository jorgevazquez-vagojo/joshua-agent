"""Tests for integrations: git, notifications, trackers."""

import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from joshua.integrations.git import GitOps
from joshua.integrations.notifications import (
    TelegramNotifier, SlackNotifier, WebhookNotifier, NullNotifier, notifier_factory,
)
from joshua.integrations.trackers import (
    JiraTracker, GitHubTracker, FilesystemTracker, NullTracker, tracker_factory,
)


class TestGitOps:
    def test_init(self, tmp_dir):
        git = GitOps(str(tmp_dir))
        assert git.cwd == str(tmp_dir)

    def test_is_repo_false(self, tmp_dir):
        git = GitOps(str(tmp_dir))
        assert git.is_repo() is False

    def test_is_repo_true(self, tmp_dir):
        import subprocess
        subprocess.run(["git", "init"], cwd=str(tmp_dir), capture_output=True)
        git = GitOps(str(tmp_dir))
        assert git.is_repo() is True

    def test_is_clean_empty_repo(self, tmp_dir):
        import subprocess
        subprocess.run(["git", "init"], cwd=str(tmp_dir), capture_output=True)
        git = GitOps(str(tmp_dir))
        assert git.is_clean() is True

    def test_is_clean_with_changes(self, tmp_dir):
        import subprocess
        subprocess.run(["git", "init"], cwd=str(tmp_dir), capture_output=True)
        (tmp_dir / "file.txt").write_text("hello")
        git = GitOps(str(tmp_dir))
        assert git.is_clean() is False

    def test_commit_all(self, tmp_dir):
        import subprocess
        subprocess.run(["git", "init"], cwd=str(tmp_dir), capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=str(tmp_dir), capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=str(tmp_dir), capture_output=True,
        )
        (tmp_dir / "file.txt").write_text("hello")

        git = GitOps(str(tmp_dir))
        assert git.commit_all("initial commit") is True
        assert git.is_clean() is True

    def test_commit_all_nothing_to_commit(self, tmp_dir):
        import subprocess
        subprocess.run(["git", "init"], cwd=str(tmp_dir), capture_output=True)
        git = GitOps(str(tmp_dir))
        assert git.commit_all("empty") is False

    def test_snapshot_restores_uncommitted_changes_on_branch(self, tmp_dir):
        import subprocess
        subprocess.run(["git", "init", "-b", "main"], cwd=str(tmp_dir), capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=str(tmp_dir), capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=str(tmp_dir), capture_output=True,
        )
        tracked = tmp_dir / "tracked.txt"
        tracked.write_text("base")
        subprocess.run(["git", "add", "tracked.txt"], cwd=str(tmp_dir), capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_dir), capture_output=True)

        tracked.write_text("changed")
        untracked = tmp_dir / "new.txt"
        untracked.write_text("new")

        git = GitOps(str(tmp_dir))
        branch = git.snapshot("sprint/test")

        assert branch == "sprint/test"
        assert git.current_branch() == "sprint/test"
        assert tracked.read_text() == "changed"
        assert untracked.read_text() == "new"
        stash_list = subprocess.run(
            ["git", "stash", "list"], cwd=str(tmp_dir), capture_output=True, text=True,
        )
        assert "joshua snapshot" not in stash_list.stdout


class TestNotifiers:
    def test_null_notifier(self):
        n = NullNotifier()
        n.notify("test")  # should not raise

    def test_telegram_missing_config(self):
        n = TelegramNotifier({})
        n.notify("test")  # should not raise (graceful skip)

    def test_slack_missing_config(self):
        n = SlackNotifier({})
        n.notify("test")  # should not raise

    def test_webhook_missing_config(self):
        n = WebhookNotifier({})
        n.notify("test")  # should not raise

    def test_notifier_factory_telegram(self):
        n = notifier_factory({"notifications": {"type": "telegram", "token": "x", "chat_id": "1"}})
        assert isinstance(n, TelegramNotifier)

    def test_notifier_factory_slack(self):
        n = notifier_factory({"notifications": {"type": "slack", "webhook_url": "http://x"}})
        assert isinstance(n, SlackNotifier)

    def test_notifier_factory_webhook(self):
        n = notifier_factory({"notifications": {"type": "webhook", "url": "http://x"}})
        assert isinstance(n, WebhookNotifier)

    def test_notifier_factory_none(self):
        n = notifier_factory({"notifications": {"type": "none"}})
        assert isinstance(n, NullNotifier)

    def test_notifier_factory_default(self):
        n = notifier_factory({})
        assert isinstance(n, NullNotifier)

    def test_notify_event(self):
        n = NullNotifier()
        n.notify_event("start", "Sprint started", "my-project")  # should not raise

    def test_notify_event_formats_correctly(self):
        """Test that notify_event calls notify with formatted message."""
        from unittest.mock import patch
        n = NullNotifier()
        with patch.object(n, "notify") as mock_notify:
            n.notify_event("revert", "Cycle 5 REVERTED", "app")
            mock_notify.assert_called_once()
            msg = mock_notify.call_args[0][0]
            assert "[REVERT]" in msg
            assert "app" in msg
            assert "Cycle 5" in msg


class TestTrackers:
    def test_null_tracker(self):
        t = NullTracker()
        assert t.create_issue("test", "desc") is None
        t.add_comment("id", "text")  # should not raise

    def test_filesystem_tracker_create(self, tmp_dir):
        t = FilesystemTracker({"dir": str(tmp_dir / "issues")})
        result = t.create_issue("Bug in login", "Users can't log in")
        assert result is not None
        assert Path(result).exists()
        content = Path(result).read_text()
        assert "Bug in login" in content

    def test_filesystem_tracker_comment(self, tmp_dir):
        t = FilesystemTracker({"dir": str(tmp_dir / "issues")})
        path = t.create_issue("Bug", "Description")
        t.add_comment(path, "Fixed in commit abc123")
        content = Path(path).read_text()
        assert "Fixed in commit" in content

    def test_tracker_factory_jira(self):
        t = tracker_factory({"tracker": {"type": "jira", "base_url": "https://x.atlassian.net"}})
        assert isinstance(t, JiraTracker)

    def test_tracker_factory_github(self):
        t = tracker_factory({"tracker": {"type": "github", "repo": "owner/repo"}})
        assert isinstance(t, GitHubTracker)

    def test_tracker_factory_filesystem(self, tmp_dir):
        t = tracker_factory({"tracker": {"type": "filesystem", "dir": str(tmp_dir)}})
        assert isinstance(t, FilesystemTracker)

    def test_tracker_factory_none(self):
        t = tracker_factory({"tracker": {"type": "none"}})
        assert isinstance(t, NullTracker)

    def test_tracker_factory_default(self):
        t = tracker_factory({})
        assert isinstance(t, NullTracker)

    def test_filesystem_task_queue(self, tmp_dir):
        t = FilesystemTracker({"dir": str(tmp_dir / "tasks")})
        task_dir = tmp_dir / "tasks"
        (task_dir / "001-fix-bug.md").write_text("Fix the login bug")
        (task_dir / "002-add-feature.md").write_text("Add dark mode")

        result = t.get_next_task()
        assert result is not None
        path, content = result
        assert "Fix the login bug" in content
        assert path.endswith(".wip")

        # Complete it
        t.complete_task(path)
        assert Path(path.replace(".wip", ".done")).exists()

        # Get next
        result2 = t.get_next_task()
        assert result2 is not None
        _, content2 = result2
        assert "dark mode" in content2

    def test_filesystem_task_queue_empty(self, tmp_dir):
        t = FilesystemTracker({"dir": str(tmp_dir / "empty-tasks")})
        assert t.get_next_task() is None

    def test_filesystem_fail_task(self, tmp_dir):
        t = FilesystemTracker({"dir": str(tmp_dir / "tasks")})
        task_dir = tmp_dir / "tasks"
        (task_dir / "001-task.md").write_text("A task")

        path, _ = t.get_next_task()
        t.fail_task(path)
        assert Path(path.replace(".wip", ".failed")).exists()
