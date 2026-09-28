"""ValueLedger collector.

One implementation, two scheduled tasks:

    collect --full        bootstrap and disaster recovery (bounded by --since)
    collect               daily incremental, bounded by the watermark

The daily run is self-healing: it scans "modified since watermark", not
"modified yesterday", so a laptop asleep for a week catches up unaided. The only
unrecoverable case is a gap longer than Claude's own transcript retention, which
is why the cadence matters.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from valueledger_collector import classify as clf
from valueledger_collector import outcomes, state
from valueledger_collector.client import LedgerClient
from valueledger_collector.transcripts import DEFAULT_ROOT, collect_sessions

C = {"dim": "\033[2m", "b": "\033[1m", "g": "\033[32m", "y": "\033[33m",
     "r": "\033[31m", "x": "\033[0m"}


def _c(s: str, k: str) -> str:
    return f"{C[k]}{s}{C['x']}" if sys.stdout.isatty() else s


def _pseudonym(value: str | None) -> str | None:
    """A stable, non-reversible stand-in for a name.

    The same repo maps to the same token every run, so rollups still group
    correctly, but the name itself never leaves the machine.
    """
    if not value:
        return None
    return "r-" + hashlib.sha256(value.encode()).hexdigest()[:10]


def _session_payload(s, cl, cost, model, policy_version, user_email, oc,
                     privacy: str = "standard") -> dict:
    p = {
        "session_id": s.session_id,
        "surface": s.surface,
        "user_email": user_email,
        "principal_type": "agent" if s.is_sidechain else "human",
        "parent_session_id": s.parent_session_id,
        # Strict mode treats repo and branch the way prompts are already treated:
        # the classifier reads them here, and only a pseudonym is published.
        "repo": _pseudonym(s.repo) if privacy == "strict" else s.repo,
        "branch": None if privacy == "strict" else s.branch,
        "started_at": s.started_at.isoformat() if s.started_at else None,
        "ended_at": s.ended_at.isoformat() if s.ended_at else None,
        "client_version": s.client_version,
        "collection_method": "collector",
        # Transcripts carry real token counts, so cost is measured, not guessed.
        "cost_basis": "measured",
        "usage": [{
            "record_uuid": u.record_uuid,
            "ts": u.ts.isoformat() if u.ts else None,
            "model": u.model,
            "request_id": u.request_id,
            "input_tokens": u.input_tokens,
            "output_tokens": u.output_tokens,
            "cache_read_tokens": u.cache_read_tokens,
            "cache_write_5m_tokens": u.cache_write_5m_tokens,
            "cache_write_1h_tokens": u.cache_write_1h_tokens,
            "web_search_requests": u.web_search_requests,
        } for u in s.usage],
        "outcomes": oc if privacy != "strict" else
                    [{"type": o["type"], "ref": _pseudonym(o["ref"]) or o["ref"],
                      "ts": o.get("ts")} for o in oc],
    }
    if cl:
        p["classification"] = {
            "initiative_key": cl.initiative_key,
            "task_type_key": cl.task_type_key,
            "activity_key": cl.activity_key,
            "conf_initiative": cl.conf_initiative,
            "conf_task_type": cl.conf_task_type,
            "conf_activity": cl.conf_activity,
            # The rationale is model-written prose about the conversation, so it
            # can quote specifics. Strict mode keeps the label and drops the story.
            "rationale": None if privacy == "strict" else cl.rationale,
            "classifier_model": model,
            "classifier_cost_usd": cost,
            "classifier_version": clf.CLASSIFIER_VERSION,
            "policy_version": policy_version,
            "work_units": [{"unit": w.unit, "count": w.count,
                            "detail": None if privacy == "strict" else w.detail}
                           for w in cl.work_units],
        }
    return p


def run(args) -> int:
    # Flags win, then environment, then the saved config. After `setup`, a
    # routine run needs no arguments at all.
    cfg = state.load_config()
    base = args.url or os.environ.get("VALUELEDGER_URL") or cfg.get("url")
    api_key = args.api_key or os.environ.get("VALUELEDGER_API_KEY") or cfg.get("api_key")
    user = args.user or os.environ.get("VALUELEDGER_USER") or cfg.get("user")
    if not (base and api_key and user):
        print(_c("Not configured yet. Run:  valueledger setup", "r"))
        return 2

    since = None
    if args.full:
        if args.since:
            since = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
        mode = f"full{' since ' + args.since if args.since else ''}"
    else:
        since = state.watermark()
        mode = f"incremental since {since.date() if since else 'never (first run)'}"

    print(_c(f"ValueLedger collector — {mode}", "b"))
    print(_c(f"  reading {args.root}", "dim"))
    if args.privacy == "strict":
        print(_c("  strict privacy: repository and branch names are replaced with "
                 "stable pseudonyms, and rationales are not sent", "dim"))

    if args.privacy not in ("standard", "strict"):
        print(_c("--privacy must be 'standard' or 'strict'.", "r"))
        return 2
    cfg_privacy = cfg.get("privacy")
    if cfg_privacy and args.privacy == "standard":
        args.privacy = cfg_privacy      # a packaged config can pin it

    sessions = collect_sessions(Path(args.root), since)
    if args.limit:
        sessions = sessions[:args.limit]
    if not sessions:
        print(_c("  nothing new to collect.", "dim"))
        return 0

    by_surface: dict[str, int] = {}
    for s in sessions:
        by_surface[s.surface] = by_surface.get(s.surface, 0) + 1
    usage_n = sum(len(s.usage) for s in sessions)
    print(f"  {len(sessions)} sessions "
          f"({', '.join(f'{k}: {v}' for k, v in sorted(by_surface.items()))}), "
          f"{usage_n} usage records")

    client = LedgerClient(base, api_key, user)
    try:
        policy = client.policy()
    except Exception as e:  # noqa: BLE001
        print(_c(f"  could not fetch policy: {e}", "r"))
        return 1
    pv = policy.get("policy_version")
    print(_c(f"  policy v{pv}: {len(policy.get('initiatives', []))} initiatives, "
             f"{len(policy.get('baselines', []))} baselines", "dim"))

    est = clf.estimate_cost(policy, sessions, args.model)
    if args.dry_run:
        print()
        print(_c(f"  DRY RUN — would classify {len(sessions)} sessions "
                 f"with {args.model}", "y"))
        print(_c(f"  estimated classifier cost: ${est:.4f}", "y"))
        print(_c("  nothing was sent and nothing was spent.", "dim"))
        print()
        for s in sessions[:12]:
            print(f"    {s.surface:<12} {(s.repo or '-'):<22} "
                  f"{len(s.prompts):>3} prompts  {(s.title or '')[:46]}")
        if len(sessions) > 12:
            print(_c(f"    … and {len(sessions) - 12} more", "dim"))
        return 0

    print(_c(f"  classifying with {args.model} (est. ${est:.4f})…", "dim"))
    payloads, failures, clf_cost = [], 0, 0.0
    for i, s in enumerate(sessions, 1):
        res = clf.classify(policy, s, args.model)
        if res.error:
            failures += 1
            if failures <= 3:
                print(_c(f"    ! {s.session_id[:8]}: {res.error}", "r"))
        clf_cost += res.cost_usd
        oc = outcomes.collect(s.cwd, s.started_at, s.ended_at) if not args.no_git else []
        payloads.append(_session_payload(s, res.classification, res.cost_usd,
                                         res.model, pv, user, oc, args.privacy))
        label = (res.classification.initiative_key if res.classification else None) \
            or "unclassifiable"
        print(f"    [{i:>3}/{len(sessions)}] {(s.repo or s.surface)[:20]:<20} → {label}")

    print(_c(f"  classifier spend: ${clf_cost:.4f}"
             + (f" ({failures} failed)" if failures else ""), "dim"))

    print(_c("  publishing…", "dim"))
    try:
        result = client.post_events(payloads)
    except Exception as e:  # noqa: BLE001
        print(_c(f"  publish failed, watermark NOT advanced: {e}", "r"))
        return 1
    finally:
        client.close()

    print()
    print(_c("  published", "g"))
    for k in ("sessions_created", "sessions_updated", "usage_events_added",
              "usage_events_deduped", "outcomes_added", "cost_usd_added"):
        v = result.get(k)
        if k == "cost_usd_added":
            print(f"    {k:<22} ${v:,.4f}")
        else:
            print(f"    {k:<22} {v}")
    if result.get("unpriced_models"):
        print(_c(f"    unpriced models: {result['unpriced_models']}", "y"))

    # Advance only on a successful publish — a failed flush must be retried.
    newest = max((s.path.stat().st_mtime for s in sessions), default=None)
    if newest:
        state.save_watermark(datetime.fromtimestamp(newest, tz=timezone.utc),
                             {"last_sessions": len(sessions),
                              "last_classifier_cost_usd": round(clf_cost, 6)})
        print(_c(f"    watermark advanced", "dim"))
    return 0


def setup(args) -> int:
    """Interactive first-run configuration."""
    cfg = state.load_config()
    print(_c("ValueLedger setup", "b"))
    print(_c("  Three questions, then it runs itself.\n", "dim"))

    def ask(label, key, default=None, secret=False):
        cur = cfg.get(key) or default or ""
        shown = (cur[:8] + "…") if (secret and cur) else cur
        suffix = f" [{shown}]" if cur else ""
        val = input(f"  {label}{suffix}: ").strip()
        return val or cur

    url = ask("Service URL", "url", args.url or os.environ.get("VALUELEDGER_URL"))
    api_key = ask("Organization API key", "api_key",
                  args.api_key or os.environ.get("VALUELEDGER_API_KEY"), secret=True)
    user = ask("Your email", "user", args.user or os.environ.get("VALUELEDGER_USER"))
    if not (url and api_key and user):
        print(_c("\n  All three are required.", "r"))
        return 2

    client = LedgerClient(url.rstrip("/"), api_key, user)
    try:
        policy = client.policy()
    except Exception as e:  # noqa: BLE001
        print(_c(f"\n  Could not reach the service: {str(e)[:150]}", "r"))
        print(_c("  Check the URL and key, then run setup again.", "dim"))
        return 1
    finally:
        client.close()

    path = state.save_config({"url": url.rstrip("/"), "api_key": api_key, "user": user})
    print()
    print(_c(f"  Connected. Policy v{policy.get('policy_version')} with "
             f"{len(policy.get('initiatives', []))} initiatives.", "g"))
    print(_c(f"  Saved to {path}", "dim"))
    print()
    print("  Next:")
    print("    valueledger --dry-run      see what would happen, spend nothing")
    print("    valueledger --full         classify everything on this machine")
    print("    valueledger schedule       run it daily, automatically")
    return 0


def schedule(args) -> int:
    """Install a daily launchd job (macOS) so this runs without being asked."""
    import plistlib
    import subprocess

    cfg = state.load_config()
    if not cfg.get("url"):
        print(_c("Run `valueledger setup` first.", "r"))
        return 2
    if sys.platform != "darwin":
        print(_c("Automatic scheduling is macOS-only for now. On Linux, add a cron "
                 "entry running this same command daily.", "y"))
        return 1

    label = "com.valueledger.collect"
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": label,
        "ProgramArguments": [sys.executable, "-m", "valueledger_collector.cli"],
        "EnvironmentVariables": {"PYTHONPATH": str(Path(__file__).resolve().parent.parent)},
        "StartCalendarInterval": {"Hour": args.hour, "Minute": 17},
        "RunAtLoad": False,
        "StandardOutPath": "/tmp/valueledger-collect.log",
        "StandardErrorPath": "/tmp/valueledger-collect.err",
    }
    plist_path.write_bytes(plistlib.dumps(plist))
    subprocess.run(["launchctl", "unload", str(plist_path)],
                   capture_output=True, check=False)
    r = subprocess.run(["launchctl", "load", str(plist_path)],
                       capture_output=True, text=True, check=False)
    if r.returncode != 0:
        print(_c(f"  launchctl load failed: {r.stderr.strip()[:160]}", "r"))
        return 1
    print(_c(f"  Scheduled daily at {args.hour:02d}:17.", "g"))
    print(_c(f"  {plist_path}", "dim"))
    print(_c("  Logs: /tmp/valueledger-collect.log", "dim"))
    print(_c("  Remove with: valueledger unschedule", "dim"))
    return 0


def unschedule(args) -> int:
    import subprocess
    label = "com.valueledger.collect"
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    if not plist_path.exists():
        print("  Not scheduled.")
        return 0
    subprocess.run(["launchctl", "unload", str(plist_path)],
                   capture_output=True, check=False)
    plist_path.unlink()
    print(_c("  Unscheduled.", "g"))
    return 0


def status(args) -> int:
    cfg = state.load_config()
    st = state.load()
    if not cfg:
        print(_c("Not configured. Run:  valueledger setup", "y"))
        return 0
    print(_c("ValueLedger", "b"))
    print(f"  service    {cfg.get('url')}")
    print(f"  user       {cfg.get('user')}")
    wm = st.get("watermark")
    print(f"  watermark  {wm or 'never run'}")
    print(f"  last run   {st.get('last_run', 'never')}")
    if st.get("last_sessions") is not None:
        print(f"  last batch {st['last_sessions']} sessions, "
              f"${st.get('last_classifier_cost_usd', 0):.4f} to classify")
    plist = Path.home() / "Library" / "LaunchAgents" / "com.valueledger.collect.plist"
    print(f"  scheduled  {'yes, daily' if plist.exists() else 'no'}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="valueledger", description="Collect Claude Code and Cowork sessions "
                                        "into the ValueLedger attribution ledger.")
    ap.add_argument("--url", help="AttributionService base URL")
    ap.add_argument("--api-key", help="Organization API key")
    ap.add_argument("--user", help="Your email, as it exists in the org")
    ap.add_argument("--root", default=str(DEFAULT_ROOT),
                    help="Transcript root (default: ~/.claude/projects)")
    ap.add_argument("--full", action="store_true",
                    help="Ignore the watermark and re-scan everything")
    ap.add_argument("--since", help="With --full, only from this ISO date")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what would be classified, and what it would cost")
    ap.add_argument("--model", default=clf.DEFAULT_MODEL)
    ap.add_argument("--limit", type=int, help="Cap sessions (useful for a first run)")
    ap.add_argument("--no-git", action="store_true", help="Skip git outcome capture")
    ap.add_argument("--privacy", default="standard", choices=["standard", "strict"],
                    help="strict also withholds repo/branch names and rationales")

    sub = ap.add_subparsers(dest="cmd")
    sp = sub.add_parser("setup", help="Configure this machine (asks three questions)")
    sp.add_argument("--url"); sp.add_argument("--api-key"); sp.add_argument("--user")
    sp.set_defaults(func=setup)
    sc = sub.add_parser("schedule", help="Run daily, automatically (macOS)")
    sc.add_argument("--hour", type=int, default=3)
    sc.set_defaults(func=schedule)
    sub.add_parser("unschedule", help="Stop running daily").set_defaults(func=unschedule)
    sub.add_parser("status", help="Show configuration and last run").set_defaults(func=status)

    args = ap.parse_args()
    if getattr(args, "func", None):
        return args.func(args)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
