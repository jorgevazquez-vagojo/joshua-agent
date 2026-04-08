"""Git operations: snapshot, merge, revert."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

log = logging.getLogger("joshua")


class GitOps:
    """Git operations for sprint cycle management.

    Each cycle creates a snapshot branch, and merges or reverts
    based on the QA verdict.
    """

    def __init__(self, project_dir: str):
        self.cwd = project_dir

    def _run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        cmd = ["git"] + list(args)
        return subprocess.run(
            cmd, capture_output=True, text=True, cwd=self.cwd, check=check,
        )

    def is_repo(self) -> bool:
        """Check if project_dir is a git repo."""
        result = self._run("rev-parse", "--is-inside-work-tree", check=False)
        return result.returncode == 0

    def is_clean(self) -> bool:
        """Check if working tree is clean."""
        result = self._run("status", "--porcelain", check=False)
        return result.returncode == 0 and not result.stdout.strip()

    def current_branch(self) -> str:
        """Get current branch name."""
        result = self._run("branch", "--show-current", check=False)
        return result.stdout.strip()

    def snapshot(self, branch_name: str) -> str | None:
        """Create a snapshot branch for the current cycle.

        Returns branch name if created successfully, None otherwise.
        """
        stashed = False
        try:
            # Stash any uncommitted changes first
            if not self.is_clean():
                result = self._run(
                    "stash", "push", "--include-untracked", "-m", f"joshua snapshot {branch_name}",
                    check=False,
                )
                if result.returncode != 0:
                    log.error(f"Failed to stash local changes: {result.stderr}")
                    return None
                stashed = "No local changes to save" not in result.stdout

            self._run("checkout", "-b", branch_name)
            if stashed:
                applied = self._run("stash", "apply", "stash@{0}", check=False)
                if applied.returncode != 0:
                    log.error(f"Failed to restore stashed changes on snapshot branch: {applied.stderr}")
                    return None
                self._run("stash", "drop", "stash@{0}", check=False)
            log.info(f"Created snapshot branch: {branch_name}")
            return branch_name
        except subprocess.CalledProcessError as e:
            log.error(f"Failed to create snapshot: {e.stderr}")
            if stashed:
                self._run("stash", "apply", "stash@{0}", check=False)
                self._run("stash", "drop", "stash@{0}", check=False)
            return None

    def detect_main_branch(self) -> str:
        """Detect the primary branch (main, master, develop, trunk...)."""
        for candidate in ("main", "master", "develop", "trunk"):
            result = self._run("rev-parse", "--verify", candidate, check=False)
            if result.returncode == 0:
                return candidate
        # Fallback: use current branch
        return self.current_branch() or "main"

    def merge_to_main(self, branch_name: str, main_branch: str | None = None) -> bool:
        """Merge snapshot branch back to main (auto-detects primary branch)."""
        target = main_branch or self.detect_main_branch()
        try:
            self._run("checkout", target)
            self._run("merge", branch_name, "--no-ff", "-m",
                       f"Merge sprint cycle: {branch_name}")
            log.info(f"Merged {branch_name} to {target}")
            return True
        except subprocess.CalledProcessError as e:
            log.error(f"Merge failed: {e.stderr}")
            self._run("merge", "--abort", check=False)
            self._run("checkout", target, check=False)
            return False

    def revert(self, branch_name: str, main_branch: str | None = None) -> bool:
        """Discard a snapshot branch (REVERT verdict)."""
        target = main_branch or self.detect_main_branch()
        try:
            self._run("checkout", target)
            self._run("branch", "-D", branch_name)
            log.info(f"Reverted: deleted branch {branch_name}")
            return True
        except subprocess.CalledProcessError as e:
            log.warning(f"Revert cleanup failed: {e.stderr}")
            self._run("checkout", target, check=False)
            return False

    def commit_all(self, message: str) -> bool:
        """Stage all changes and commit."""
        try:
            self._run("add", "-A")
            if self.is_clean():
                log.info("No changes to commit")
                return False
            self._run("commit", "-m", message)
            return True
        except subprocess.CalledProcessError:
            return False

    def get_head_sha(self) -> str | None:
        """Get the current HEAD commit SHA."""
        result = self._run("rev-parse", "HEAD", check=False)
        if result.returncode == 0:
            return result.stdout.strip()
        return None

    def reset_hard(self, ref: str) -> bool:
        """Hard reset to a specific commit (used by hillclimb on REVERT)."""
        try:
            self._run("reset", "--hard", ref)
            self._run("clean", "-fd")
            log.info(f"Hard reset to {ref[:12]}")
            return True
        except subprocess.CalledProcessError as e:
            log.error(f"Reset failed: {e.stderr}")
            return False

    def push(self, remote: str = "origin", branch: str | None = None) -> bool:
        """Push to remote."""
        try:
            cmd = ["push", remote]
            if branch:
                cmd.append(branch)
            self._run(*cmd)
            return True
        except subprocess.CalledProcessError as e:
            log.error(f"Push failed: {e.stderr}")
            return False
