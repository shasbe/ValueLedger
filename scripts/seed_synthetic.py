"""Generate synthetic activity for an org so the dashboard shows a full picture.

Adds sessions for the named users only, leaving everyone else's real data alone.
Session ids are prefixed so they can never collide with a collector upload, and
the whole batch is removable by prefix.

Coverage is deliberate rather than random: every initiative, task type and
activity gets real spend, because a dashboard with empty rows reads as broken
rather than as a demo.
"""
from __future__ import annotations

import argparse
import random
import sys
from datetime import datetime, timedelta, timezone

import httpx

PREFIX = "syn"

# model: (weight, in, out, cache_read, cache_w5m, cache_w1h) token ranges
MODELS = [
    ("claude-opus-5",   0.40, (2, 400), (300, 4500), (4_000, 70_000), (0, 5_000), (0, 14_000)),
    ("claude-sonnet-5", 0.38, (2, 300), (250, 3200), (3_000, 45_000), (0, 4_000), (0, 9_000)),
    ("claude-haiku-4-5", 0.22, (2, 200), (150, 1800), (1_500, 22_000), (0, 2_000), (0, 5_000)),
]

# Which activities plausibly go with which task type. Filtered at runtime against
# the activities the org actually defines, so a key that does not exist is never
# sent — the server would silently drop it and the row would look unclassified.
TASK_ACTIVITY = {
    "coding": ["feature", "bugfix", "refactor", "test", "review", "scaffolding", "deploy"],
    "strategy-doc": ["docs", "exploration", "review"],
    "data-analysis": ["exploration", "docs", "feature"],
    "research": ["exploration", "docs"],
    "marketing-artifact": ["docs", "feature"],
    "ops-support": ["bugfix", "deploy", "exploration"],
}

UNITS = {"coding": "merged_pr", "strategy-doc": "document", "data-analysis": "analysis",
         "research": "brief", "marketing-artifact": "slide", "ops-support": "incident"}

RATIONALES = {
    "coding": ["Implemented {w} and brought the test suite back to green.",
               "Traced a regression in {w} and shipped the fix.",
               "Refactored {w} to remove a layer of indirection."],
    "strategy-doc": ["Drafted the {w} proposal, including the phased rollout.",
                     "Wrote up options for {w} with a recommendation.",
                     "Turned the {w} discussion into a decision record."],
    "data-analysis": ["Queried usage to size the {w} opportunity.",
                      "Built the {w} funnel breakdown and checked it against billing."],
    "research": ["Compared vendors for {w} and wrote a short brief.",
                 "Read through the {w} literature and summarised what applies."],
    "marketing-artifact": ["Produced the {w} launch deck.",
                           "Wrote customer-facing copy for {w}."],
    "ops-support": ["Worked a production incident affecting {w}.",
                    "Chased a deployment failure in {w} to root cause."],
}

TOPICS = {
    "sales": ["a customer POC", "the Salesforce sync", "a prospect pilot", "a demo environment"],
    "data-platform": ["the ingestion pipeline", "dbt models", "warehouse partitioning",
                      "data quality checks"],
    "developer-platform": ["the CI pipeline", "the build cache", "service scaffolding",
                           "local dev environments"],
    "plg": ["self-serve onboarding", "the free-tier upgrade path", "in-product activation",
            "trial conversion"],
    "virtualdb": ["the virtual graph engine", "ontology mapping", "the embedded runtime",
                  "graph projection"],
    "marketing": ["the Q4 campaign", "the launch microsite", "customer segmentation",
                  "the webinar series"],
    "engineering": ["dependency upgrades", "flaky test cleanup", "log noise reduction",
                    "an on-call runbook"],
    "unknown": ["an internal tool", "a one-off request", "a spike"],
}


def usage_for(rng, started, n):
    out = []
    for i in range(n):
        r, acc = rng.random(), 0.0
        for name, w, inp, outp, cr, cw5, cw1 in MODELS:
            acc += w
            if r <= acc:
                break
        out.append({
            "record_uuid": f"u{i}", "ts": (started + timedelta(minutes=i)).isoformat(),
            "model": name,
            "input_tokens": rng.randint(*inp), "output_tokens": rng.randint(*outp),
            "cache_read_tokens": rng.randint(*cr),
            "cache_write_5m_tokens": rng.randint(*cw5),
            "cache_write_1h_tokens": rng.randint(*cw1),
        })
    return out


def build(rng, users, inits, days, per_user, activities=None, task_types=None):
    """Every (initiative x task type) pair gets at least one session, then fill."""
    global TASK_ACTIVITY
    if activities:
        TASK_ACTIVITY = {t: [a for a in acts if a in activities] or sorted(activities)
                         for t, acts in TASK_ACTIVITY.items()}
    if task_types:
        TASK_ACTIVITY = {t: a for t, a in TASK_ACTIVITY.items() if t in task_types}
    combos = [(i, t) for i in inits for t in TASK_ACTIVITY]
    rng.shuffle(combos)
    plan = [(rng.choice(users), i, t) for i, t in combos]
    while len(plan) < len(users) * per_user:
        plan.append((rng.choice(users), rng.choice(inits), rng.choice(list(TASK_ACTIVITY))))
    rng.shuffle(plan)

    now = datetime.now(timezone.utc)
    sessions = []
    for n, (user, init, task) in enumerate(plan):
        act = rng.choice(TASK_ACTIVITY[task])
        day = now - timedelta(days=rng.randint(0, days - 1))
        # weekday-weighted, working hours
        if day.weekday() >= 5 and rng.random() < 0.72:
            day -= timedelta(days=rng.randint(1, 3))
        started = day.replace(hour=rng.randint(8, 19), minute=rng.randint(0, 59),
                              second=0, microsecond=0)
        dur = timedelta(minutes=rng.randint(6, 165))
        surface = rng.choices(["claude_code", "cowork", "chat"], weights=[.66, .2, .14])[0]
        chat = surface == "chat"
        topic = rng.choice(TOPICS.get(init, ["internal work"]))

        conf = round(rng.uniform(0.55, 0.98), 2)
        unclass = rng.random() < 0.05

        s = {
            "session_id": f"{PREFIX}-{n:05d}",
            "surface": surface,
            "user_email": user,
            "principal_type": "agent" if rng.random() < 0.05 else "human",
            "repo": None if chat else rng.choice(
                ["payments-svc", "web-app", "analytics", "infra", "graph-core",
                 "pipelines", "tooling", "mobile"]),
            "branch": None if chat else f"{init[:4]}-{rng.randint(100, 9999)}",
            "started_at": started.isoformat(),
            "ended_at": (started + dur).isoformat(),
            "client_version": "2.1.281",
            "collection_method": "mcp_skill" if chat else "collector",
            "cost_basis": "unknown" if chat else "measured",
            "usage": [] if chat else usage_for(rng, started, rng.randint(40, 340)),
            "outcomes": [],
        }

        # Outcomes: this is what makes dark spend and cost-per-PR mean anything.
        if not chat and rng.random() > 0.11:
            for _ in range(rng.randint(1, 7)):
                s["outcomes"].append({"type": "commit", "ref": f"{rng.randrange(16**7):07x}",
                                      "ts": (started + dur).isoformat()})
            if rng.random() < 0.58:
                pr = rng.randint(100, 9999)
                s["outcomes"].append({"type": "pr_opened", "ref": f"#{pr}",
                                      "ts": (started + dur).isoformat()})
                if rng.random() < 0.8:
                    s["outcomes"].append({"type": "pr_merged", "ref": f"#{pr}",
                                          "ts": (started + dur + timedelta(hours=5)).isoformat()})
            if rng.random() < 0.33:
                s["outcomes"].append({"type": "test_added", "ref": f"t{rng.randint(1,999)}",
                                      "ts": (started + dur).isoformat()})
            if rng.random() < 0.05:
                s["outcomes"].append({"type": "revert", "ref": f"{rng.randrange(16**7):07x}",
                                      "ts": (started + dur + timedelta(days=2)).isoformat()})

        units = []
        if not unclass and rng.random() < 0.72:
            merged = sum(1 for o in s["outcomes"] if o["type"] == "pr_merged")
            unit = UNITS[task]
            count = merged if unit == "merged_pr" else rng.randint(1, 6)
            if count:
                units = [{"unit": unit, "count": float(count),
                          "detail": f"{count} {unit} on {topic}"}]

        s["classification"] = {
            "initiative_key": None if unclass else init,
            "task_type_key": None if unclass else task,
            "activity_key": None if unclass else act,
            "conf_initiative": None if unclass else conf,
            "conf_task_type": None if unclass else round(rng.uniform(.62, .98), 2),
            "conf_activity": None if unclass else round(rng.uniform(.6, .97), 2),
            "rationale": ("Nothing in the current policy describes this work."
                          if unclass else
                          rng.choice(RATIONALES[task]).format(w=topic)),
            "classifier_model": "claude-haiku-4-5",
            "classifier_cost_usd": round(rng.uniform(0.0008, 0.0042), 6),
            "classifier_version": "clf-0.1.0",
            "work_units": units,
        }
        sessions.append(s)
    return sessions


def main() -> int:
    ap = argparse.ArgumentParser(description="Add synthetic activity to a ValueLedger org.")
    ap.add_argument("--url", required=True)
    ap.add_argument("--api-key", required=True)
    ap.add_argument("--admin", required=True)
    ap.add_argument("--exclude", nargs="*", default=[])
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--per-user", type=int, default=52)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--purge", action="store_true",
                    help="Remove previously generated synthetic sessions first")
    a = ap.parse_args()

    base = a.url.rstrip("/")
    h = {"X-API-Key": a.api_key, "X-User-Email": a.admin, "Content-Type": "application/json"}
    c = httpx.Client(timeout=180)

    users = [u["email"] for u in c.get(f"{base}/v1/users", headers=h).json()
             if u["email"] not in a.exclude]
    inits = [i["key"] for i in c.get(f"{base}/v1/initiatives", headers=h).json()]
    acts = {x["key"] for x in c.get(f"{base}/v1/activities", headers=h).json()}
    tts = {x["key"] for x in c.get(f"{base}/v1/task-types", headers=h).json()}
    print(f"  {len(users)} users, {len(inits)} initiatives, {len(tts)} task types, "
          f"{len(acts)} activities")

    if a.purge:
        rows = c.get(f"{base}/v1/ledger", headers=h, params={"limit": 2000}).json()["sessions"]
        gone = 0
        for r in rows:
            if r["session_id"].startswith(PREFIX + "-"):
                c.delete(f"{base}/v1/sessions/{r['session_id']}", headers=h)
                gone += 1
        print(f"  purged {gone} previously generated sessions")

    rng = random.Random(a.seed)
    sessions = build(rng, users, inits, a.days, a.per_user, acts, tts)
    print(f"  generated {len(sessions)} sessions; publishing in batches")

    added = cost = 0.0
    for i in range(0, len(sessions), 40):
        r = c.post(f"{base}/v1/events", headers=h, json={"sessions": sessions[i:i + 40]})
        r.raise_for_status()
        j = r.json()
        added += j["sessions_created"]
        cost += j["cost_usd_added"]
        print(f"    batch {i//40 + 1}: +{j['sessions_created']} sessions, "
              f"${j['cost_usd_added']:,.2f}, {j['outcomes_added']} outcomes")
    print(f"\n  {int(added)} sessions, ${cost:,.2f} added")
    return 0


if __name__ == "__main__":
    sys.exit(main())
