"""Check a ValueLedger config for problems before it reaches a service.

Three severities:
  ERROR  apply will fail, or the data will be wrong
  WARN   apply succeeds but something will behave in a way you did not intend
  NOTE   worth a look; a judgement call, not a defect
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROLES = {"member", "initiative_owner", "finance", "admin"}
SOURCE_TYPES = {"customer_measured", "team_survey", "industry_estimate",
                "vendor_claim", "unset"}
STATUSES = {"active", "archived", "paused"}
KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Guidance that separates adjacent initiatives usually says what is excluded.
EXCLUSION_HINTS = ("does not", "doesn't", "do not", "not include", "rather than",
                   "instead of", "exclude", "except", "as opposed to", "not for",
                   "belongs to", "in case of conflict", "prioritize", "prioritise",
                   "takes precedence", "goes to")

C = {"e": "\033[31m", "w": "\033[33m", "n": "\033[2m", "g": "\033[32m",
     "b": "\033[1m", "x": "\033[0m"}


class Report:
    def __init__(self):
        self.items = []

    def add(self, sev, where, msg, fix=None):
        self.items.append((sev, where, msg, fix))


def _load(p: Path, rep, name):
    if not p.exists():
        rep.add("ERROR", name, "file is missing")
        return None
    try:
        return yaml.safe_load(p.read_text())
    except yaml.YAMLError as e:
        rep.add("ERROR", name, f"is not valid YAML: {str(e)[:160]}")
        return None


def validate(cfg: Path) -> Report:
    rep = Report()
    org = _load(cfg / "org.yaml", rep, "org.yaml")
    inits = _load(cfg / "initiatives.yaml", rep, "initiatives.yaml")
    tax = _load(cfg / "taxonomy.yaml", rep, "taxonomy.yaml")
    base = _load(cfg / "baselines.yaml", rep, "baselines.yaml")
    users = _load(cfg / "users.yaml", rep, "users.yaml")
    if inits is None or users is None or tax is None:
        return rep

    # ---- org ----
    if org:
        if not (org.get("name") or "").strip() or "CHANGE" in str(org.get("name", "")):
            rep.add("ERROR", "org.yaml", "name is unset or still a placeholder")
        cap = org.get("session_minutes_cap")
        if cap is not None and not (5 <= cap <= 480):
            rep.add("ERROR", "org.yaml", f"session_minutes_cap {cap} is outside 5-480")

    # ---- users ----
    emails, ccs = {}, {}
    for i, u in enumerate(users or []):
        w = f"users.yaml[{i}]"
        e = (u.get("email") or "").strip().lower()
        if not EMAIL_RE.match(e):
            rep.add("ERROR", w, f"invalid email: {u.get('email')!r}")
            continue
        if e in emails:
            rep.add("ERROR", w, f"duplicate user {e}")
        emails[e] = u
        if u.get("role") not in ROLES:
            rep.add("ERROR", w, f"role {u.get('role')!r} is not one of {sorted(ROLES)}")
        if u.get("cost_center"):
            ccs.setdefault(u["cost_center"], []).append(e)
        m = (u.get("manager_email") or "").strip().lower()
        if m and not EMAIL_RE.match(m):
            rep.add("WARN", w, f"manager_email {m!r} is not a valid address")
    for e, u in emails.items():
        m = (u.get("manager_email") or "").strip().lower()
        if m and m not in emails:
            rep.add("NOTE", f"users.yaml {e}", f"manager {m} is not in this file",
                    "harmless, but org charts built from it will have a hole")
    if not any(u.get("role") == "admin" for u in emails.values()):
        rep.add("ERROR", "users.yaml", "no admin — nobody can change the policy")
    if not any(u.get("role") == "finance" for u in emails.values()):
        rep.add("WARN", "users.yaml", "no finance role — nobody sees org-wide totals")

    # ---- initiatives ----
    keys = {}
    init_ccs = set()
    for i, it in enumerate(inits or []):
        w = f"initiatives.yaml[{i}] {it.get('key','?')}"
        k = (it.get("key") or "").strip()
        if not KEY_RE.match(k):
            rep.add("ERROR", w, f"key {k!r} must be lowercase letters, digits and hyphens")
        if k in keys:
            rep.add("ERROR", w, f"duplicate key {k}")
        keys[k] = it
        if "CHANGE" in k or "CHANGE" in str(it.get("name", "")):
            rep.add("ERROR", w, "still a placeholder from `init`")
        if not (it.get("name") or "").strip():
            rep.add("ERROR", w, "name is empty")

        owner = (it.get("owner_email") or "").strip().lower()
        if not owner:
            rep.add("WARN", w, "no owner_email — nobody can open this initiative's report")
        elif owner not in emails:
            rep.add("ERROR", w, f"owner {owner} does not exist in users.yaml",
                    "apply will create the initiative but the owner cannot read it")
        else:
            role = emails[owner].get("role")
            if role not in ("initiative_owner", "finance", "admin"):
                rep.add("ERROR", w, f"owner {owner} has role '{role}', so cannot read "
                        f"their own initiative", "set their role to initiative_owner")

        g = (it.get("classification_guidance") or "").strip()
        if len(g) < 40:
            rep.add("ERROR", w, "classification_guidance is too short to classify against")
        else:
            if not any(h in g.lower() for h in EXCLUSION_HINTS):
                rep.add("NOTE", w, "guidance says what belongs but never what does not",
                        "exclusions are what stop adjacent initiatives taking each "
                        "other's spend")
            if g.rstrip() != g or "  " in g:
                rep.add("NOTE", w, "guidance has trailing or doubled whitespace")

        if it.get("status") and it["status"] not in STATUSES:
            rep.add("ERROR", w, f"status {it['status']!r} is not one of {sorted(STATUSES)}")
        b = it.get("budget_amount")
        if b is not None and (not isinstance(b, (int, float)) or b <= 0):
            rep.add("ERROR", w, f"budget_amount {b!r} must be a positive number")
        if b and not it.get("budget_period"):
            rep.add("WARN", w, "has a budget but no budget_period")
        if it.get("cost_center"):
            init_ccs.add(it["cost_center"])

    for cc in sorted(init_ccs - set(ccs)):
        owners = [k for k, v in keys.items() if v.get("cost_center") == cc]
        rep.add("WARN", f"cost center {cc}",
                f"used by {', '.join(owners)} but no user belongs to it",
                "chargeback by cost center will show this as unattributed")

    # ---- taxonomy ----
    tt_keys = {t.get("key") for t in (tax.get("task_types") or [])}
    for group in ("task_types", "activities"):
        seen = set()
        for i, t in enumerate(tax.get(group) or []):
            w = f"taxonomy.yaml {group}[{i}] {t.get('key','?')}"
            if t.get("key") in seen:
                rep.add("ERROR", w, "duplicate key")
            seen.add(t.get("key"))
            if len((t.get("classification_guidance") or "").strip()) < 20:
                rep.add("WARN", w, "guidance is very short")
        if not seen:
            rep.add("ERROR", "taxonomy.yaml", f"{group} is empty")

    # ---- baselines ----
    pairs = set()
    for i, b in enumerate(base or []):
        w = f"baselines.yaml[{i}] {b.get('task_type_key','?')}/{b.get('unit','?')}"
        tk = b.get("task_type_key")
        if tk not in tt_keys:
            rep.add("ERROR", w, f"task_type_key {tk!r} is not in taxonomy.yaml",
                    "apply will reject this baseline with a 400")
        pair = (tk, b.get("unit"))
        if pair in pairs:
            rep.add("ERROR", w, "duplicate task_type + unit")
        pairs.add(pair)
        m, lo, hi = b.get("human_minutes_per_unit"), b.get("minutes_low"), b.get("minutes_high")
        if not isinstance(m, (int, float)) or m <= 0:
            rep.add("ERROR", w, f"human_minutes_per_unit {m!r} must be positive")
        if lo is not None and hi is not None:
            if lo > hi:
                rep.add("ERROR", w, f"minutes_low {lo} exceeds minutes_high {hi}")
            elif isinstance(m, (int, float)) and not (lo <= m <= hi):
                rep.add("ERROR", w, f"human_minutes_per_unit {m} is outside its own "
                        f"range {lo}-{hi}")
            elif hi and lo and hi / max(lo, 1) < 1.3:
                rep.add("NOTE", w, "the low/high range is very narrow",
                        "it asserts more confidence than most baselines deserve")
        st = b.get("source_type", "unset")
        if st not in SOURCE_TYPES:
            rep.add("ERROR", w, f"source_type {st!r} is not one of {sorted(SOURCE_TYPES)}")
        elif st in ("unset", "vendor_claim") or not (b.get("source") or "").strip():
            rep.add("WARN", w, f"source_type is '{st}' with no credible source",
                    "the policy will report as not ready and the figure as illustrative")

    covered = {p[0] for p in pairs}
    for tk in sorted(tt_keys - covered):
        rep.add("NOTE", f"baselines.yaml", f"task type '{tk}' has no baseline",
                "work of that type contributes cost but no productivity")
    return rep


def main() -> int:
    cfg = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("config")
    tty = sys.stdout.isatty()
    def col(s, k): return f"{C[k]}{s}{C['x']}" if tty else s

    rep = validate(cfg)
    print(col(f"ValueLedger config — validate", "b"))
    print(col(f"  {cfg}", "n"))
    print()
    order = {"ERROR": 0, "WARN": 1, "NOTE": 2}
    counts = {"ERROR": 0, "WARN": 0, "NOTE": 0}
    for sev, where, msg, fix in sorted(rep.items, key=lambda x: (order[x[0]], x[1])):
        counts[sev] += 1
        k = {"ERROR": "e", "WARN": "w", "NOTE": "n"}[sev]
        print(col(f"  {sev:<5}", k) + f" {where}")
        print(f"        {msg}")
        if fix:
            print(col(f"        -> {fix}", "n"))
    if not rep.items:
        print(col("  no problems found.", "g"))
    print()
    print(f"  {counts['ERROR']} errors, {counts['WARN']} warnings, {counts['NOTE']} notes")
    return 1 if counts["ERROR"] else 0


if __name__ == "__main__":
    sys.exit(main())
