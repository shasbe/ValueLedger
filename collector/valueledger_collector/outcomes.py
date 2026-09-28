"""Verifiable output, read from git.

This is the half of the ratio nobody else can produce: cost is joined to things
that actually happened in a repository, not to self-reported effort. Only
artifacts that exist are reported — if a session produced nothing, it produced
nothing, and that is the dark-spend signal.
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timedelta
from pathlib import Path

TEST_HINTS = ("test_", "_test.", "/tests/", "spec.", ".spec.", "conftest")


def _git(cwd: Path, *args: str, timeout: int = 15) -> str:
    try:
        r = subprocess.run(["git", "-C", str(cwd), *args],
                           capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def is_repo(cwd: Path) -> bool:
    return bool(_git(cwd, "rev-parse", "--is-inside-work-tree").strip() == "true")


def collect(cwd: str | None, start: datetime | None, end: datetime | None,
            author_email: str | None = None) -> list[dict]:
    """Commits in the session window, plus derived file/test signals.

    The window is padded on the end because commits often land a little after the
    last assistant message.
    """
    if not cwd or not start:
        return []
    p = Path(cwd)
    if not p.exists() or not is_repo(p):
        return []

    since = (start - timedelta(minutes=5)).isoformat()
    until = ((end or start) + timedelta(hours=2)).isoformat()

    args = ["log", f"--since={since}", f"--until={until}",
            "--pretty=format:%H%x1f%s", "--no-merges"]
    if author_email:
        args.append(f"--author={author_email}")
    log = _git(p, *args)
    if not log.strip():
        return []

    out: list[dict] = []
    touched_test = False
    files_total = 0
    for line in log.splitlines():
        if "\x1f" not in line:
            continue
        sha, subject = line.split("\x1f", 1)
        stat = _git(p, "show", "--name-only", "--pretty=format:", sha)
        files = [f for f in stat.splitlines() if f.strip()]
        files_total += len(files)
        if any(any(h in f.lower() for h in TEST_HINTS) for f in files):
            touched_test = True
        out.append({"type": "commit", "ref": sha[:10],
                    "metadata": {"subject": subject[:200], "files": len(files)}})
        # A revert is a negative outcome and must be visible: it is what makes
        # rework spend real rather than theoretical.
        if subject.lower().startswith("revert"):
            out.append({"type": "revert", "ref": sha[:10],
                        "metadata": {"subject": subject[:200]}})

    if files_total:
        out.append({"type": "file_changed", "ref": f"{files_total}-files",
                    "metadata": {"count": files_total}})
    if touched_test:
        out.append({"type": "test_added", "ref": "tests-touched", "metadata": {}})
    return out


def git_user_email(cwd: str | None) -> str | None:
    if not cwd or not Path(cwd).exists():
        return None
    return _git(Path(cwd), "config", "user.email").strip() or None
