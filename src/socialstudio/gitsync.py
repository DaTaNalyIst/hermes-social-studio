"""Commit queue/state changes and push them, safely shared by Kaggle and GitHub Actions.

Writers never merge files. `transact(fn)` fetches origin, resets to it, applies the change
function, commits and pushes; if someone else pushed first, it starts over from the new tip.
That makes every change an operation re-applied on fresh state, so concurrent writers
(the Kaggle notebook and the publisher) never conflict.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from .redact import redact


class GitError(RuntimeError):
    pass


def _git(root: Path, *args, check=True):
    p = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if check and p.returncode != 0:
        raise GitError(redact(f"git {' '.join(args)} failed: {(p.stderr or p.stdout)[-800:]}"))
    return p


def enabled(root: Path) -> bool:
    if os.environ.get("SOCIAL_NO_GIT") == "1":
        return False
    return (root / ".git").exists() and _git(root, "remote", check=False).stdout.strip() != ""


def branch(root: Path) -> str:
    out = _git(root, "rev-parse", "--abbrev-ref", "HEAD", check=False).stdout.strip()
    return out if out and out != "HEAD" else "main"


def ensure_identity(root: Path):
    if not _git(root, "config", "user.email", check=False).stdout.strip():
        _git(root, "config", "user.email", "social-studio@users.noreply.github.com")
        _git(root, "config", "user.name", "social-studio bot")


MANAGED = ("queue", "state", "studio.toml", "autopilot.toml")


def _foreign_changes(root: Path, paths) -> list[str]:
    out = _git(root, "status", "--porcelain", "--untracked-files=no").stdout.splitlines()
    files = [line[3:].strip() for line in out if line.strip()]
    return [f for f in files if not any(f == p or f.startswith(p.rstrip("/") + "/") for p in paths)]


def transact(root: Path, message: str, fn, paths=MANAGED, tries: int = 6):
    """Apply fn() on top of the latest remote state and push. Returns fn's result.
    Without git (or SOCIAL_NO_GIT=1) just runs fn()."""
    root = Path(root)
    if not enabled(root):
        return fn()
    ensure_identity(root)
    br = branch(root)
    foreign = _foreign_changes(root, paths)
    if foreign:
        raise GitError("uncommitted edits outside queue/state would be overwritten: "
                       + ", ".join(foreign[:8]) + ". Commit or discard them first.")
    for attempt in range(tries):
        _git(root, "fetch", "-q", "origin", br)
        dirty = _git(root, "status", "--porcelain", "--", *[p for p in paths if (root / p).exists()]).stdout.strip() \
            if any((root / p).exists() for p in paths) else ""
        if dirty:
            # Uncommitted local edits in queue/state would be lost by the reset: keep them aside.
            _git(root, "stash", "push", "-q", "-u", "-m", "social-studio autosave", "--", *[p for p in paths if (root / p).exists()], check=False)
        _git(root, "reset", "-q", "--hard", f"origin/{br}")
        result = fn()
        present = [p for p in paths if (root / p).exists() or _git(root, "ls-files", "--", p, check=False).stdout.strip()]
        if present:
            _git(root, "add", "-A", "--", *present)
        if not _git(root, "diff", "--cached", "--quiet", check=False).returncode:
            return result  # nothing changed
        _git(root, "commit", "-q", "-m", message)
        push = _git(root, "push", "-q", "origin", f"HEAD:{br}", check=False)
        if push.returncode == 0:
            return result
        time.sleep(1 + attempt * 2)
    raise GitError(f"could not push after {tries} tries (someone keeps pushing?)")


def pull(root: Path):
    """Fast-forward to the remote before reading state."""
    if not enabled(root):
        return
    br = branch(root)
    _git(root, "fetch", "-q", "origin", br)
    _git(root, "merge", "-q", "--ff-only", f"origin/{br}", check=False)
