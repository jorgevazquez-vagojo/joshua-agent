"""Git operations: snapshot, merge, revert."""

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
        try:
            # Stash any uncommitted changes first
            if not self.is_clean():
                self._run("stash", "--include-untracked")

            self._run("checkout", "-b", branch_name)
            log.info(f"Created snapshot branch: {branch_name}")
            return branch_name
        except subprocess.CalledProcessError as e:
            log.error(f"Failed to create snapshot: {e.stderr}")
            return None

    def merge_to_main(self, branch_name: str, main_branch: str = "main") -> bool:
        """Merge snapshot branch back to main.

        Returns True if merge was successful.
        """
        try:
            self._run("checkout", main_branch)
            self._run("merge", branch_name, "--no-ff", "-m",
                       f"Merge sprint cycle: {branch_name}")
            log.info(f"Merged {branch_name} to {main_branch}")
            return True
        except subprocess.CalledProcessError as e:
            log.error(f"Merge failed: {e.stderr}")
            # Abort merge if in conflict
            self._run("merge", "--abort", check=False)
            self._run("checkout", main_branch, check=False)
            return False

    def revert(self, branch_name: str, main_branch: str = "main") -> bool:
        """Discard a snapshot branch (REVERT verdict)."""
        try:
            self._run("checkout", main_branch)
            self._run("branch", "-D", branch_name)
            log.info(f"Reverted: deleted branch {branch_name}")
            return True
        except subprocess.CalledProcessError as e:
            log.warning(f"Revert cleanup failed: {e.stderr}")
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
