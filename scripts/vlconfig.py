"""ValueLedger configuration as files.

The admin UI is fine for a demo and wrong for real setup: an initiative's
classification guidance is a paragraph of prose that wants drafting, reviewing
and version control, not a textarea. These files are the source of truth; the
service is what they are applied to.

    vlconfig export          pull a service's current state into config/
    vlconfig apply           push config/ to a service
    vlconfig apply --prune   also delete what the files no longer define
    vlconfig diff            show what apply would change

Apply is idempotent: every write is an upsert keyed on `key` (or `email`), so
running it twice changes nothing the second time.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx
import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

C = {"b": "\033[1m", "d": "\033[2m", "g": "\033[32m", "y": "\033[33m",
     "r": "\033[31m", "x": "\033[0m"}


def c(s, k):
    return f"{C[k]}{s}{C['x']}" if sys.stdout.isatty() else s


class Client:
    def __init__(self, url, api_key, user):
        self.base = url.rstrip("/")
        self.h = {"X-API-Key": api_key, "X-User-Email": user,
                  "Content-Type": "application/json"}
        self.http = httpx.Client(timeout=90)

    def get(self, path):
        r = self.http.get(self.base + path, headers=self.h)
        r.raise_for_status()
        return r.json()

    def post(self, path, body):
        r = self.http.post(self.base + path, headers=self.h, json=body)
        r.raise_for_status()
        return r.json()

    def put(self, path, body=None, params=None):
        r = self.http.put(self.base + path, headers=self.h, json=body, params=params)
        r.raise_for_status()
        return r.json()

    def delete(self, path):
        r = self.http.delete(self.base + path, headers=self.h)
        r.raise_for_status()
        return r.json()


# --------------------------------------------------------------------------
# YAML helpers — keep long prose readable as block scalars
# --------------------------------------------------------------------------

class _Block(str):
    pass


def _block_representer(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(data), style="|")


yaml.add_representer(_Block, _block_representer)


def _prose(v):
    """Multi-sentence text becomes a block scalar so it stays editable."""
    if isinstance(v, str) and (len(v) > 70 or "\n" in v):
        return _Block(v.strip() + "\n")
    return v


def dump(path: Path, data, header: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.dump(data, sort_keys=False, allow_unicode=True, width=88,
                     default_flow_style=False)
    path.write_text("# " + header.replace("\n", "\n# ") + "\n\n" + body)


def load(path: Path):
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text())


# --------------------------------------------------------------------------
# export
# --------------------------------------------------------------------------

def export(cl: Client, out: Path) -> None:
    org = cl.get("/v1/org")
    policy = cl.get("/v1/policy")

    dump(out / "org.yaml", {
        "name": org["name"],
        "privacy_mode": org.get("privacy_mode", "local"),
        "session_minutes_cap": org.get("session_minutes_cap", 90),
        "global_guidance": _prose(policy.get("global_guidance", "")),
        "output_extraction_guidance": _prose(policy.get("output_extraction_guidance", "")),
    }, "Organization settings and the two prompts every classification runs with.\n"
       "session_minutes_cap bounds how much of a session's wall-clock counts as\n"
       "human attention, because a session left open overnight is not eight hours.")

    inits = cl.get("/v1/initiatives")
    dump(out / "initiatives.yaml", [{
        "key": i["key"],
        "name": i["name"],
        "owner_email": i.get("owner_email"),
        "cost_center": i.get("cost_center"),
        "budget_amount": i.get("budget_amount"),
        "budget_period": i.get("budget_period"),
        "status": i.get("status", "active"),
        "classification_guidance": _prose(i.get("classification_guidance", "")),
    } for i in inits],
        "The initiatives spend is attributed to. This is the file you will edit most.\n\n"
        "classification_guidance is a PROMPT, not a description: it is sent verbatim to\n"
        "the classifier. Say what belongs, and say what does NOT - the exclusions do most\n"
        "of the work in separating adjacent initiatives.")

    dump(out / "taxonomy.yaml", {
        "task_types": [{"key": t["key"], "name": t["name"],
                        "classification_guidance": _prose(t.get("classification_guidance", ""))}
                       for t in cl.get("/v1/task-types")],
        "activities": [{"key": a["key"], "name": a["name"],
                        "classification_guidance": _prose(a.get("classification_guidance", ""))}
                       for a in cl.get("/v1/activities")],
    }, "What KIND of work (task type) and the finer verb (activity).\n"
       "These change rarely; initiatives change often.")

    dump(out / "baselines.yaml", [{
        "task_type_key": b["task_type_key"],
        "unit": b["unit"],
        "unit_plural": b.get("unit_plural"),
        "human_minutes_per_unit": b["human_minutes_per_unit"],
        "minutes_low": b.get("minutes_low"),
        "minutes_high": b.get("minutes_high"),
        "source": b.get("source", ""),
        "source_type": b.get("source_type", "unset"),
    } for b in cl.get("/v1/baselines")],
        "The ONE assumption in the product: what a unit of work costs a human.\n\n"
        "source_type is ordered by how much weight a reader should give it:\n"
        "  customer_measured  you measured it\n"
        "  team_survey        the team self-reported\n"
        "  industry_estimate  external benchmark\n"
        "  vendor_claim       vendor-supplied, treat with caution\n"
        "  unset              no source - the product will say the figure is illustrative\n\n"
        "minutes_low/high produce the sensitivity range. Make them honest: a narrow\n"
        "range asserts more confidence than most baselines deserve.")

    dump(out / "users.yaml", [{
        "email": u["email"], "display_name": u.get("display_name"),
        "role": u.get("role", "member"), "cost_center": u.get("cost_center"),
        "manager_email": u.get("manager_email"),
    } for u in cl.get("/v1/users")],
        "People, and what each may read.\n"
        "  member            only their own sessions\n"
        "  initiative_owner  initiatives where owner_email matches\n"
        "  finance           everything, read-only\n"
        "  admin             everything, plus policy and taxonomy")

    print(c(f"  exported to {out}", "g"))
    for f in sorted(out.glob("*.yaml")):
        print(f"    {f.name:<20} {f.stat().st_size:>6} bytes")


# --------------------------------------------------------------------------
# apply
# --------------------------------------------------------------------------

def _plan(cl: Client, cfg_dir: Path):
    """What apply would do. Returns a list of (verb, what, detail)."""
    acts = []
    org_cfg = load(cfg_dir / "org.yaml") or {}
    live_org = cl.get("/v1/org")
    if org_cfg.get("session_minutes_cap") and \
            org_cfg["session_minutes_cap"] != live_org.get("session_minutes_cap"):
        acts.append(("update", "org", f"session_minutes_cap -> {org_cfg['session_minutes_cap']}"))
    live_policy = cl.get("/v1/policy")
    for k in ("global_guidance", "output_extraction_guidance"):
        if (org_cfg.get(k) or "").strip() and \
                (org_cfg[k] or "").strip() != (live_policy.get(k) or "").strip():
            acts.append(("update", "policy", k))

    def cmp_keyed(path, cfg, idkey, label):
        live = {x[idkey]: x for x in cl.get(path)}
        seen = set()
        for item in cfg or []:
            k = item.get(idkey)
            if not k:
                continue
            seen.add(k)
            acts.append(("create" if k not in live else "update", label, k))
        for k in live:
            if k not in seen:
                acts.append(("prune", label, k))

    cmp_keyed("/v1/initiatives", load(cfg_dir / "initiatives.yaml"), "key", "initiative")
    tax = load(cfg_dir / "taxonomy.yaml") or {}
    cmp_keyed("/v1/task-types", tax.get("task_types"), "key", "task type")
    cmp_keyed("/v1/activities", tax.get("activities"), "key", "activity")
    cmp_keyed("/v1/users", load(cfg_dir / "users.yaml"), "email", "user")
    return acts


def apply(cl: Client, cfg_dir: Path, prune: bool, dry: bool) -> int:
    acts = _plan(cl, cfg_dir)
    if dry:
        if not acts:
            print(c("  nothing to change.", "d"))
            return 0
        for verb, what, detail in acts:
            if verb == "prune" and not prune:
                print(c(f"    {'extra':>7}  {what:<12} {detail}   (use --prune to delete)", "d"))
            else:
                col = {"create": "g", "update": "y", "prune": "r"}[verb]
                print(c(f"    {verb:>7}  {what:<12} {detail}", col))
        return 0

    n = 0
    org_cfg = load(cfg_dir / "org.yaml") or {}
    if org_cfg.get("session_minutes_cap"):
        cl.put("/v1/org/settings", params={"session_minutes_cap": org_cfg["session_minutes_cap"]})
        n += 1
    body = {}
    for k in ("global_guidance", "output_extraction_guidance"):
        if (org_cfg.get(k) or "").strip():
            body[k] = org_cfg[k].strip()
    if body:
        cl.put("/v1/policy", body)
        n += 1

    # Users first: an initiative's owner must exist before it names them.
    for u in load(cfg_dir / "users.yaml") or []:
        cl.post("/v1/users", u)
        n += 1
    tax = load(cfg_dir / "taxonomy.yaml") or {}
    for t in tax.get("task_types") or []:
        cl.post("/v1/task-types", {**t, "description": t.get("classification_guidance", "")})
        n += 1
    for a in tax.get("activities") or []:
        cl.post("/v1/activities", {**a, "description": a.get("classification_guidance", "")})
        n += 1
    for i in load(cfg_dir / "initiatives.yaml") or []:
        cl.post("/v1/initiatives", {**i, "description": i.get("classification_guidance", "")})
        n += 1
    # Baselines last: they reference task types.
    for b in load(cfg_dir / "baselines.yaml") or []:
        cl.post("/v1/baselines", {**b, "active": True})
        n += 1

    # Budgets mirror the initiative's own amount, so the report finds a Budget row.
    live_inits = {i["key"]: i for i in cl.get("/v1/initiatives")}
    for i in load(cfg_dir / "initiatives.yaml") or []:
        if i.get("budget_amount") and i["key"] in live_inits:
            cl.post("/v1/budgets", {"scope_type": "initiative",
                                    "scope_id": live_inits[i["key"]]["id"],
                                    "period": i.get("budget_period") or "current",
                                    "amount_usd": float(i["budget_amount"])})
            n += 1

    if prune:
        for verb, what, detail in acts:
            if verb != "prune":
                continue
            path = {"initiative": "/v1/initiatives", "task type": "/v1/task-types",
                    "activity": "/v1/activities", "user": "/v1/users"}[what]
            idkey = "email" if what == "user" else "key"
            live = {x[idkey]: x for x in cl.get(path)}
            if detail in live:
                cl.delete(f"{path}/{live[detail]['id']}")
                print(c(f"    pruned {what} {detail}", "r"))
                n += 1

    st = cl.get("/v1/policy/status")
    print()
    print(c(f"  applied {n} changes. Policy is now v{st['policy_version']}.", "g"))
    if st["problems"]:
        print(c("  policy is not ready to classify against:", "y"))
        for p in st["problems"]:
            print(c(f"    - {p}", "y"))
    else:
        print(c(f"  policy ready: {st['counts']['initiatives']} initiatives, "
                f"{st['counts']['task_types']} task types, "
                f"{st['counts']['activities']} activities, "
                f"{st['counts']['baselines']} baselines.", "d"))
    return 0


STD_TASK_TYPES = [
    ("coding", "Coding", "Writing, modifying, reviewing or debugging code, tests, "
     "infrastructure or build configuration."),
    ("strategy-doc", "Strategy Doc", "Specs, designs, RFCs, architecture proposals, plans "
     "and written decision records."),
    ("data-analysis", "Data Analysis", "Querying, exploring, modelling or visualising data "
     "to answer a question."),
    ("research", "Research", "Investigating options, reading documentation, evaluating "
     "tools or vendors, and spikes that inform a later decision."),
    ("marketing-artifact", "Marketing Artifact", "Customer-facing copy, launch content, "
     "release notes, landing pages and campaign material."),
    ("ops-support", "Ops / Support", "Incident response, production debugging, deployment "
     "problems and customer escalations."),
]

STD_ACTIVITIES = [
    ("feature", "Feature Development", "Building new user-visible capability."),
    ("bugfix", "Bug Fix", "Correcting defective behaviour in existing code."),
    ("refactor", "Refactor", "Restructuring existing code without changing behaviour."),
    ("test", "Testing", "Writing or repairing automated tests."),
    ("docs", "Documentation", "Writing or updating documentation, specs and READMEs."),
    ("review", "Code Review", "Reviewing changes authored by someone else."),
    ("exploration", "Exploration", "Open-ended investigation with no committed artifact "
     "expected. Legitimate work, not the same as waste."),
    ("scaffolding", "Scaffolding", "Project setup, boilerplate and generated structure."),
    ("deploy", "Deployment", "Building, releasing, configuring infrastructure and getting "
     "things running."),
]


def init(out: Path) -> None:
    """Write a starter config for a real organization."""
    if out.exists() and any(out.glob("*.yaml")):
        print(c(f"  {out} already holds config files. Point --dir somewhere else, or "
                f"move them aside.", "r"))
        return

    from app_defaults import DEFAULT_GLOBAL_GUIDANCE, DEFAULT_EXTRACTION_GUIDANCE

    dump(out / "org.yaml", {
        "name": "CHANGE ME",
        "privacy_mode": "local",
        "session_minutes_cap": 90,
        "global_guidance": _Block(DEFAULT_GLOBAL_GUIDANCE.strip() + "\n"),
        "output_extraction_guidance": _Block(DEFAULT_EXTRACTION_GUIDANCE.strip() + "\n"),
    }, "Organization settings and the two prompts every classification runs with.\n"
       "The defaults below are good; change `name` and leave the rest until you have\n"
       "seen how it classifies.")

    dump(out / "initiatives.yaml", [
        {"key": "CHANGE-ME-1", "name": "Your first initiative",
         "owner_email": "owner@yourcompany.com", "cost_center": "CC-1000",
         "budget_amount": None, "budget_period": "2026-Q4", "status": "active",
         "classification_guidance": _Block(
             "Describe what belongs to this initiative the way you would explain it to a "
             "new joiner: the systems, the repos, the kind of problem.\n\n"
             "Then say what does NOT belong, naming the initiative it belongs to instead. "
             "The exclusions do most of the work - they are what stops two adjacent "
             "initiatives collecting each other's spend.\n")},
        {"key": "CHANGE-ME-2", "name": "Your second initiative",
         "owner_email": "owner@yourcompany.com", "cost_center": "CC-2000",
         "budget_amount": None, "budget_period": "2026-Q4", "status": "active",
         "classification_guidance": _Block("...\n")},
    ], "The initiatives spend is attributed to. This is the file you will edit most.\n\n"
       "classification_guidance is a PROMPT, not a description: it is sent verbatim to\n"
       "the classifier.\n\n"
       "Start with 5-10 initiatives that match how your organization already talks about\n"
       "its work. Anything that fits none of them is recorded as `unclassifiable`, which\n"
       "is the signal that one is missing - not an error.")

    dump(out / "taxonomy.yaml", {
        "task_types": [{"key": k, "name": n, "classification_guidance": _prose(g)}
                       for k, n, g in STD_TASK_TYPES],
        "activities": [{"key": k, "name": n, "classification_guidance": _prose(g)}
                       for k, n, g in STD_ACTIVITIES],
    }, "What KIND of work (task type) and the finer verb (activity).\n"
       "These defaults suit most organizations. Change them last, not first.")

    dump(out / "baselines.yaml", [
        {"task_type_key": "coding", "unit": "merged_pr", "unit_plural": "merged PRs",
         "human_minutes_per_unit": 180, "minutes_low": 90, "minutes_high": 420,
         "source": "CHANGE ME - where did this number come from?",
         "source_type": "unset"},
    ], "The ONE assumption in the product: what a unit of work costs a human.\n\n"
       "Leave this file empty until you have a number you can defend. Productivity\n"
       "simply will not be reported, which is better than reporting a guess.\n\n"
       "source_type, ordered by how much weight a reader should give it:\n"
       "  customer_measured  you measured it\n"
       "  team_survey        the team self-reported\n"
       "  industry_estimate  external benchmark\n"
       "  vendor_claim       vendor-supplied, treat with caution\n"
       "  unset              no source - the product marks the figure illustrative\n"
       "                     and refuses to call the policy ready")

    dump(out / "users.yaml", [
        {"email": "you@yourcompany.com", "display_name": "You",
         "role": "admin", "cost_center": "CC-1000", "manager_email": None},
    ], "People, and what each may read.\n"
       "  member            only their own sessions\n"
       "  initiative_owner  initiatives where owner_email matches\n"
       "  finance           everything, read-only\n"
       "  admin             everything, plus policy and taxonomy\n\n"
       "Everyone whose work you collect needs a row here, and an initiative's\n"
       "owner_email must match one of them.")

    print(c(f"  starter config written to {out}", "g"))
    for f in sorted(out.glob("*.yaml")):
        print(f"    {f.name}")
    print()
    print("  Edit initiatives.yaml and users.yaml first, then:")
    print("    vlconfig diff     see what would change")
    print("    vlconfig apply    push it")


def main() -> int:
    ap = argparse.ArgumentParser(prog="vlconfig",
                                 description="Manage ValueLedger configuration as files.")
    ap.add_argument("command", choices=["init", "export", "apply", "diff"])
    ap.add_argument("--url", default=os.environ.get("VALUELEDGER_URL"))
    ap.add_argument("--api-key", default=os.environ.get("VALUELEDGER_API_KEY"))
    ap.add_argument("--user", default=os.environ.get("VALUELEDGER_ADMIN"),
                    help="An admin email in the org")
    ap.add_argument("--dir", default=str(CONFIG_DIR))
    ap.add_argument("--prune", action="store_true",
                    help="Delete anything the files no longer define")
    a = ap.parse_args()

    out_early = Path(a.dir)
    if a.command == "init":
        print(c("ValueLedger config \u2014 init", "b"))
        print()
        init(out_early)
        return 0

    if not (a.url and a.api_key and a.user):
        print(c("Need --url, --api-key and --user (or VALUELEDGER_URL / "
                "VALUELEDGER_API_KEY / VALUELEDGER_ADMIN).", "r"))
        return 2

    cl = Client(a.url, a.api_key, a.user)
    out = Path(a.dir)
    print(c(f"ValueLedger config — {a.command}", "b"))
    print(c(f"  {a.url}  as {a.user}", "d"))
    print()
    try:
        if a.command == "export":
            export(cl, out)
        elif a.command == "diff":
            apply(cl, out, prune=a.prune, dry=True)
        else:
            apply(cl, out, prune=a.prune, dry=False)
    except httpx.HTTPStatusError as e:
        print(c(f"  {e.response.status_code}: {e.response.text[:200]}", "r"))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
