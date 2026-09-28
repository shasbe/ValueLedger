"""Watermark.

A full run and a daily run are the same code with a different bound, so the only
thing that distinguishes them lives here. The watermark advances on success
only — a failed flush must be retried, not skipped.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

STATE_DIR = Path.home() / ".valueledger"
STATE_FILE = STATE_DIR / "state.json"

# A sent package carries its own config.json beside the code, so a recipient
# never types a URL or a key. VALUELEDGER_CONFIG points at it.
CONFIG_FILE = Path(os.environ.get("VALUELEDGER_CONFIG")
                   or (STATE_DIR / "config.json"))


def load_config() -> dict:
    """Settings written once by `valueledger setup`, so routine runs take no flags."""
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def save_config(cfg: dict) -> Path:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
    CONFIG_FILE.chmod(0o600)          # it holds an org key
    return CONFIG_FILE


def load() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def watermark() -> datetime | None:
    v = load().get("watermark")
    if not v:
        return None
    try:
        return datetime.fromisoformat(v).astimezone(timezone.utc)
    except ValueError:
        return None


def save_watermark(ts: datetime, extra: dict | None = None) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    data = load()
    data["watermark"] = ts.astimezone(timezone.utc).isoformat()
    data["last_run"] = datetime.now(timezone.utc).isoformat()
    if extra:
        data.update(extra)
    STATE_FILE.write_text(json.dumps(data, indent=2))
