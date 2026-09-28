"""Read what Claude already wrote to disk.

Claude Code (CLI and desktop) and Cowork all persist the same JSONL format under
`~/.claude/projects/`, so one parser covers both surfaces. Nothing here
intercepts a running session — the collector reads finished transcripts.

Two parsing facts that are easy to get wrong and were verified against real
transcripts:

  * A typed human prompt is a `user` record whose `message.content` is a
    **string**. Tool results are `user` records whose content is a **list**.
    That is the only reliable separator — `promptSource` is usually null.
  * Usage lives on `assistant` records and cache creation is split by TTL
    (`ephemeral_5m_input_tokens` / `ephemeral_1h_input_tokens`), which price
    differently. Summing them loses money.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_ROOT = Path.home() / ".claude" / "projects"

# Cowork keeps its working directories under the app's scratch workspaces.
COWORK_MARKER = "scratch-workspaces"


@dataclass
class UsageRecord:
    record_uuid: str
    ts: datetime | None
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_5m_tokens: int = 0
    cache_write_1h_tokens: int = 0
    web_search_requests: int = 0
    request_id: str | None = None


@dataclass
class ParsedSession:
    session_id: str
    path: Path
    surface: str
    cwd: str | None = None
    repo: str | None = None
    branch: str | None = None
    client_version: str | None = None
    is_sidechain: bool = False
    parent_session_id: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    prompts: list[str] = field(default_factory=list)
    usage: list[UsageRecord] = field(default_factory=list)
    title: str | None = None

    @property
    def prompt_chars(self) -> int:
        return sum(len(p) for p in self.prompts)

    @property
    def has_content(self) -> bool:
        return bool(self.prompts) and bool(self.usage)


def _ts(v) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _surface(cwd: str | None, path: Path) -> str:
    blob = f"{cwd or ''} {path}"
    return "cowork" if COWORK_MARKER in blob else "claude_code"


def _repo_from_cwd(cwd: str | None) -> str | None:
    if not cwd:
        return None
    name = Path(cwd).name
    return name or None


def discover(root: Path = DEFAULT_ROOT, since: datetime | None = None) -> list[Path]:
    """Transcripts modified since the watermark.

    Modification time, not session start: a session resumed days later should be
    re-read so its later turns are captured.
    """
    if not root.exists():
        return []
    out = []
    for p in root.rglob("*.jsonl"):
        try:
            mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if since is None or mtime > since:
            out.append(p)
    return sorted(out)


def parse(path: Path) -> ParsedSession | None:
    """Parse one transcript. Returns None if it holds nothing usable."""
    session_id = path.stem
    s = ParsedSession(session_id=session_id, path=path, surface="claude_code")
    seen_usage: set[str] = set()

    try:
        raw_lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return None

    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue

        rtype = rec.get("type")

        # Context can appear on any record; first non-null wins.
        if s.cwd is None and rec.get("cwd"):
            s.cwd = rec["cwd"]
        if s.branch is None and rec.get("gitBranch"):
            s.branch = rec["gitBranch"]
        if s.client_version is None and rec.get("version"):
            s.client_version = rec["version"]
        if rec.get("isSidechain"):
            s.is_sidechain = True
            if rec.get("parentUuid"):
                s.parent_session_id = rec["parentUuid"]

        ts = _ts(rec.get("timestamp"))
        if ts:
            if s.started_at is None or ts < s.started_at:
                s.started_at = ts
            if s.ended_at is None or ts > s.ended_at:
                s.ended_at = ts

        if rtype in ("ai-title", "custom-title"):
            s.title = s.title or rec.get("aiTitle") or rec.get("customTitle")
            continue

        if rtype == "last-prompt" and not s.title:
            lp = rec.get("lastPrompt")
            if lp:
                s.title = lp[:120]
            continue

        if rtype == "user":
            content = (rec.get("message") or {}).get("content")
            # String content = a human typed it. List content = tool results.
            if isinstance(content, str):
                text = content.strip()
                if text:
                    s.prompts.append(text)
            continue

        if rtype == "assistant":
            msg = rec.get("message") or {}
            usage = msg.get("usage") or {}
            if not usage:
                continue
            uid = rec.get("uuid") or msg.get("id")
            if not uid or uid in seen_usage:
                continue          # transcripts repeat assistant records
            seen_usage.add(uid)
            cc = usage.get("cache_creation") or {}
            st = usage.get("server_tool_use") or {}
            s.usage.append(UsageRecord(
                record_uuid=uid,
                ts=ts,
                model=msg.get("model") or "unknown",
                input_tokens=int(usage.get("input_tokens") or 0),
                output_tokens=int(usage.get("output_tokens") or 0),
                cache_read_tokens=int(usage.get("cache_read_input_tokens") or 0),
                cache_write_5m_tokens=int(cc.get("ephemeral_5m_input_tokens") or 0),
                cache_write_1h_tokens=int(cc.get("ephemeral_1h_input_tokens") or 0),
                web_search_requests=int(st.get("web_search_requests") or 0),
                request_id=msg.get("id"),
            ))

    s.surface = _surface(s.cwd, path)
    s.repo = _repo_from_cwd(s.cwd)
    return s if s.has_content else None


def collect_sessions(root: Path = DEFAULT_ROOT,
                     since: datetime | None = None) -> list[ParsedSession]:
    out = []
    for p in discover(root, since):
        parsed = parse(p)
        if parsed:
            out.append(parsed)
    return out
