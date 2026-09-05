import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


def detect_git_branch(workspace: str) -> Optional[str]:
    """Detect the current git branch for the given workspace.
    
    Returns the branch name if the workspace is inside a git repository,
    or None if no git repo is found. Falls back to reading .git/HEAD
    directly if the `git` command is unavailable.
    """
    if not workspace or not os.path.isdir(workspace):
        return None

    # Method 1: Use `git rev-parse --abbrev-ref HEAD` (most reliable)
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0:
            branch = result.stdout.strip()
            # Detached HEAD returns "HEAD"; try to resolve to a tag/describe
            if branch and branch != "HEAD":
                return branch
            # Detached HEAD - try describe, fall back to short SHA
            desc = subprocess.run(
                ["git", "describe", "--tags", "--exact-match", "HEAD"],
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=2,
            )
            if desc.returncode == 0 and desc.stdout.strip():
                return f"tag:{desc.stdout.strip()}"
            sha = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=2,
            )
            if sha.returncode == 0 and sha.stdout.strip():
                return f"detached:{sha.stdout.strip()}"
            return "HEAD"
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    # Method 2: Fallback - read .git/HEAD directly (walks up to find .git dir)
    try:
        current = os.path.abspath(workspace)
        for _ in range(20):  # Limit traversal depth
            git_head = os.path.join(current, ".git", "HEAD")
            if os.path.isfile(git_head):
                with open(git_head, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                if content.startswith("ref: refs/heads/"):
                    return content[len("ref: refs/heads/"):]
                return None
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
    except (OSError, UnicodeDecodeError):
        pass

    return None


@dataclass
class AppState:
    """Application state container for UI metadata."""
    model: str = "darktech_v3"
    workspace: str = "."
    branch: Optional[str] = None  # None means no git repo detected
    settings: dict = field(default_factory=dict)

    def set_model(self, model: str) -> None:
        """Change the current model."""
        self.model = model

    def set_workspace(self, path: str) -> None:
        """Set workspace directory and auto-detect git branch."""
        self.workspace = path
        # Auto-detect git branch for the new workspace
        self.branch = detect_git_branch(path)

    def set_branch(self, branch: str) -> None:
        """Set git branch."""
        self.branch = branch

    def refresh_branch(self) -> None:
        """Re-detect the git branch for the current workspace."""
        self.branch = detect_git_branch(self.workspace)
