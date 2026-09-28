"""Synthetic 90-day enterprise, so the service and dashboard are demoable before
the collector exists.

Deliberately not uniform: initiatives differ in dark-spend rate, chat traffic is
classified but unpriced, and a slice of sessions land in the review queue. A demo
where every number looks healthy proves nothing.
"""
from __future__ import annotations

import argparse
import random
import sys
from datetime import datetime, timedelta, timezone

from app.auth import hash_key, new_api_key
from app.db import SessionLocal, init_db
from app.models import (
    Activity, Budget, Classification, Initiative, Org, OutcomeEvent, ProductivityBaseline,
    SessionRow, TaskType, UsageEvent, User, WorkOutput,
)
from app.policy import DEFAULT_GLOBAL_GUIDANCE, bump_policy_version
from app.pricing import price_usage_event, seed_price_book

COST_CENTERS = ["CC-1000 Engineering", "CC-2000 Data", "CC-3000 Growth", "CC-4000 Platform"]

INITIATIVES = [
    ("payments-migration", "Payments Migration", "CC-1000 Engineering",
     "The Stripe→Adyen cutover. Includes the payments-svc and billing-gateway repos, "
     "webhook handling, and reconciliation jobs. Does NOT include general billing UI "
     "work — that is Billing UX.", 12000.0, ["payments-svc", "billing-gateway"], 0.12),
    ("billing-ux", "Billing UX", "CC-3000 Growth",
     "Customer-facing billing screens, invoice rendering, plan selection and upgrade "
     "flows. Front-end work in the web-app repo. Not the payment rails themselves.",
     6000.0, ["web-app"], 0.22),
    ("data-platform", "Data Platform", "CC-2000 Data",
     "The warehouse, dbt models, ingestion pipelines and data quality checks. Anything "
     "in the analytics or pipelines repos.", 9000.0, ["analytics", "pipelines"], 0.18),
    ("developer-platform", "Developer Platform", "CC-4000 Platform",
     "Internal tooling, CI/CD, build systems, developer environments and the service "
     "scaffolding templates. The infra and tooling repos.", 7500.0, ["infra", "tooling"], 0.28),
    ("fraud-detection", "Fraud Detection", "CC-2000 Data",
     "Risk scoring models, rules engine, and the fraud review console. The risk-engine "
     "repo and its notebooks.", 8000.0, ["risk-engine"], 0.15),
    ("mobile-checkout", "Mobile Checkout", "CC-3000 Growth",
     "The iOS and Android checkout experience, including wallet integrations and "
     "in-app purchase flows. The mobile repo.", 5500.0, ["mobile"], 0.20),
]

TASK_TYPES = [
    ("coding", "Coding", "Writing, modifying, reviewing or debugging production code, "
     "tests, infrastructure-as-code or build configuration."),
    ("strategy-doc", "Strategy Doc", "Planning documents, technical designs, RFCs, "
     "architecture proposals, roadmaps and decision records."),
    ("data-analysis", "Data Analysis", "Querying, exploring, modelling or visualising "
     "data to answer a question. Includes notebooks and ad-hoc SQL."),
    ("marketing-artifact", "Marketing Artifact", "Customer-facing copy, launch content, "
     "release notes, landing pages and campaign material."),
    ("research", "Research", "Investigating options, reading documentation, evaluating "
     "libraries or vendors, and spikes that inform a later decision."),
    ("ops-support", "Ops / Support", "Incident response, on-call investigation, "
     "production debugging, and customer escalations."),
]

ACTIVITIES = [
    ("feature", "Feature Development", "Building new user-visible capability."),
    ("bugfix", "Bug Fix", "Correcting defective behaviour in existing code."),
    ("refactor", "Refactor", "Restructuring existing code without changing behaviour."),
    ("test", "Testing", "Writing or repairing automated tests."),
    ("docs", "Documentation", "Writing or updating documentation and comments."),
    ("review", "Code Review", "Reviewing changes authored by someone else."),
    ("exploration", "Exploration", "Open-ended investigation with no committed artifact "
     "expected. Legitimate work — not the same as waste."),
    ("scaffolding", "Scaffolding", "Project setup, boilerplate, and generated structure."),
]

# task_type_key, unit, plural, minutes, low, high, source, source_type
BASELINES = [
    ("coding", "merged_pr", "merged PRs", 240.0, 120.0, 480.0,
     "2026 engineering cycle-time study, 6 months of PR data", "customer_measured"),
    ("marketing-artifact", "slide", "slides", 10.0, 7.0, 15.0,
     "2026 design team time study", "customer_measured"),
    ("strategy-doc", "document", "documents", 180.0, 90.0, 360.0,
     "Product team self-report, Q2 2026 retro", "team_survey"),
    ("data-analysis", "analysis", "analyses", 120.0, 60.0, 240.0,
     "Analytics team self-report", "team_survey"),
    ("research", "brief", "briefs", 90.0, 45.0, 180.0,
     "Industry benchmark, not validated internally", "industry_estimate"),
    ("ops-support", "incident", "incidents", 45.0, 20.0, 120.0,
     "Incident log median, 2026 H1", "customer_measured"),
]

# How many units a session of each task type tends to produce. Coding is not here
# because it uses measured merged PRs rather than a model-extracted count.
UNIT_YIELD = {
    "marketing-artifact": ("slide", 3, 14),
    "strategy-doc": ("document", 0, 2),
    "data-analysis": ("analysis", 1, 3),
    "research": ("brief", 0, 1),
    "ops-support": ("incident", 1, 2),
}

FIRST = ["ana", "ben", "chen", "dana", "eli", "fatima", "gus", "hana", "ivan", "jo",
         "kira", "luis", "mira", "noah", "omar", "priya", "quinn", "rosa", "sam", "tara"]
LAST = ["adams", "brooks", "cruz", "diaz", "evans", "flores", "gupta", "hayes", "ito", "jones"]

MODELS = [("claude-opus-5", 0.45), ("claude-sonnet-5", 0.35), ("claude-haiku-4-5", 0.20)]


def _pick_model(rng: random.Random) -> str:
    r = rng.random()
    acc = 0.0
    for m, w in MODELS:
        acc += w
        if r <= acc:
            return m
    return MODELS[-1][0]


def seed_into_org(db, org: Org, days: int = 90, n_users: int = 40,
                  seed_val: int = 7) -> dict:
    """Generate synthetic data inside an existing org. Split out from seed() so a
    deployed instance can be populated over the API without shell access."""
    rng = random.Random(seed_val)
    seed_price_book(db)
    if True:
        # ---- users ----
        users: list[User] = []
        emails = set()
        while len(users) < n_users:
            e = f"{rng.choice(FIRST)}.{rng.choice(LAST)}@example.com"
            if e in emails:
                continue
            emails.add(e)
            u = User(org_id=org.id, email=e, display_name=e.split("@")[0].replace(".", " ").title(),
                     cost_center=rng.choice(COST_CENTERS), role="member")
            users.append(u)
            db.add(u)
        db.add(User(org_id=org.id, email="admin@example.com", display_name="Platform Admin",
                    cost_center="CC-4000 Platform", role="admin"))
        db.add(User(org_id=org.id, email="cfo@example.com", display_name="Finance",
                    cost_center="CC-4000 Platform", role="finance"))
        db.flush()

        # ---- taxonomy ----
        inits: list[Initiative] = []
        for key, name, cc, guidance, budget, repos, dark_rate in INITIATIVES:
            owner = rng.choice(users)
            owner.role = "initiative_owner"
            i = Initiative(org_id=org.id, key=key, name=name, description=guidance,
                           classification_guidance=guidance, owner_email=owner.email,
                           budget_amount=budget, budget_period="2026-Q3",
                           cost_center=cc, status="active", policy_version=1)
            db.add(i)
            inits.append(i)
            db.flush()
            db.add(Budget(org_id=org.id, scope_type="initiative", scope_id=i.id,
                          period="2026-Q3", amount_usd=budget))
        for key, name, desc in TASK_TYPES:
            db.add(TaskType(org_id=org.id, key=key, name=name, description=desc,
                            classification_guidance=desc, policy_version=1))
        for key, name, desc in ACTIVITIES:
            db.add(Activity(org_id=org.id, key=key, name=name, description=desc,
                            classification_guidance=desc, policy_version=1))
        for tk, unit, plural, mins, lo, hi, src, stype in BASELINES:
            db.add(ProductivityBaseline(
                org_id=org.id, task_type_key=tk, unit=unit, unit_plural=plural,
                human_minutes_per_unit=mins, minutes_low=lo, minutes_high=hi,
                source=src, source_type=stype, set_by="admin@example.com", active=True))
        db.flush()
        version = bump_policy_version(db, org.id, "admin@example.com")
        db.flush()

        task_types = db.query(TaskType).filter(TaskType.org_id == org.id).all()
        activities = db.query(Activity).filter(Activity.org_id == org.id).all()
        repo_by_init = {k: repos for k, _n, _c, _g, _b, repos, _d in INITIATIVES}
        dark_by_init = {k: d for k, _n, _c, _g, _b, _r, d in INITIATIVES}

        now = datetime.now(timezone.utc)
        n_sessions = 0
        for day in range(days):
            ts_day = now - timedelta(days=days - day)
            # Weekday-weighted volume, so the trend line looks like real work.
            base = 14 if ts_day.weekday() < 5 else 3
            for _ in range(rng.randint(max(1, base - 5), base + 6)):
                init = rng.choices(inits, weights=[5, 3, 4, 3, 2, 2])[0]
                user = rng.choice(users)
                surface = rng.choices(["claude_code", "cowork", "chat"],
                                      weights=[0.70, 0.18, 0.12])[0]
                started = ts_day.replace(hour=rng.randint(8, 19),
                                         minute=rng.randint(0, 59), second=0, microsecond=0)
                dur = timedelta(minutes=rng.randint(4, 180))
                repo = rng.choice(repo_by_init[init.key]) if surface != "chat" else None

                # Chat has no local transcript and the model cannot see its own
                # usage, so it is classified but never priced.
                cost_basis = "unknown" if surface == "chat" else "measured"
                collection = "mcp_skill" if surface == "chat" else "collector"

                s = SessionRow(
                    org_id=org.id, session_id=f"sess-{day:03d}-{n_sessions:05d}",
                    surface=surface, user_email=user.email,
                    principal_type="agent" if rng.random() < 0.06 else "human",
                    repo=repo, branch=(f"{init.key[:3]}-{rng.randint(1000, 9999)}"
                                       if repo else None),
                    started_at=started, ended_at=started + dur,
                    client_version="2.1.281",
                    collection_method=collection, cost_basis=cost_basis,
                    external_ref=None if surface != "chat" else f"conv-{n_sessions:05d}")
                db.add(s)
                db.flush()
                n_sessions += 1

                # ---- usage (measured surfaces only) ----
                if cost_basis == "measured":
                    for r in range(rng.randint(3, 40)):
                        model = _pick_model(rng)
                        ev = UsageEvent(
                            session_pk=s.id, record_uuid=f"{s.session_id}-r{r}",
                            ts=started + timedelta(minutes=r),
                            model=model,
                            input_tokens=rng.randint(2, 400),
                            output_tokens=rng.randint(200, 4000),
                            cache_read_tokens=rng.randint(5_000, 60_000),
                            cache_write_5m_tokens=rng.randint(0, 4_000),
                            cache_write_1h_tokens=rng.randint(0, 12_000))
                        price_usage_event(db, ev)
                        db.add(ev)

                # ---- classification ----
                conf = round(rng.uniform(0.55, 0.99), 2)
                unclassifiable = rng.random() < 0.04
                chosen_tt = rng.choice(task_types)
                cl = Classification(
                    session_pk=s.id,
                    initiative_id=None if unclassifiable else init.id,
                    task_type_id=None if unclassifiable else chosen_tt.id,
                    activity_id=None if unclassifiable else rng.choice(activities).id,
                    conf_initiative=None if unclassifiable else conf,
                    conf_task_type=None if unclassifiable else round(rng.uniform(0.6, 0.99), 2),
                    conf_activity=None if unclassifiable else round(rng.uniform(0.6, 0.99), 2),
                    rationale=("No initiative in the current policy describes this work."
                               if unclassifiable else
                               f"Prompts concern {init.name.lower()}; "
                               f"{'repo ' + repo if repo else 'no repo context'}."),
                    classifier_model="claude-haiku-4-5",
                    classifier_cost_usd=round(rng.uniform(0.0008, 0.0035), 6),
                    classifier_version="clf-0.3.0", policy_version=version,
                    status=("unclassifiable" if unclassifiable
                            else "needs_review" if conf < 0.7 else "auto"))
                db.add(cl)

                # ---- work outputs (the measured productivity numerator) ----
                if not unclassifiable and chosen_tt.key in UNIT_YIELD:
                    unit, lo_n, hi_n = UNIT_YIELD[chosen_tt.key]
                    n = rng.randint(lo_n, hi_n)
                    if n > 0:
                        db.add(WorkOutput(
                            session_pk=s.id, unit=unit, count=float(n),
                            basis="model_extracted",
                            detail=f"Counted from session transcript by the classifier."))

                # ---- outcomes ----
                if surface != "chat" and rng.random() > dark_by_init[init.key]:
                    for c in range(rng.randint(1, 6)):
                        db.add(OutcomeEvent(org_id=org.id, session_pk=s.id, type="commit",
                                            ref=f"{rng.randrange(16**7):07x}",
                                            ts=started + dur))
                    if rng.random() < 0.55:
                        pr = rng.randint(1000, 9999)
                        db.add(OutcomeEvent(org_id=org.id, session_pk=s.id, type="pr_opened",
                                            ref=f"#{pr}", ts=started + dur))
                        if rng.random() < 0.78:
                            db.add(OutcomeEvent(org_id=org.id, session_pk=s.id,
                                                type="pr_merged", ref=f"#{pr}",
                                                ts=started + dur + timedelta(hours=6)))
                            if not unclassifiable and chosen_tt.key == "coding":
                                db.add(WorkOutput(
                                    session_pk=s.id, unit="merged_pr", count=1.0,
                                    basis="measured",
                                    detail=f"PR #{pr} merged."))
                    if rng.random() < 0.30:
                        db.add(OutcomeEvent(org_id=org.id, session_pk=s.id, type="test_added",
                                            ref=f"test-{rng.randint(1, 999)}", ts=started + dur))
                    if rng.random() < 0.05:
                        db.add(OutcomeEvent(org_id=org.id, session_pk=s.id, type="revert",
                                            ref=f"{rng.randrange(16**7):07x}",
                                            ts=started + dur + timedelta(days=2)))
            db.flush()

        db.commit()
        total = db.query(UsageEvent).count()
        cost = sum(c or 0 for (c,) in db.query(UsageEvent.cost_usd).all())
        return {"org_id": org.id, "org_name": org.name,
                "sessions": n_sessions, "usage_events": total,
                "total_cost_usd": round(cost, 2), "policy_version": version,
                "users": n_users + 2}


def seed(org_name: str, days: int = 90, n_users: int = 40, seed_val: int = 7) -> dict:
    """Create a fresh org and fill it. Returns the API key, shown once."""
    init_db()
    db = SessionLocal()
    try:
        raw_key = new_api_key()
        org = Org(name=org_name, api_key_hash=hash_key(raw_key), privacy_mode="local")
        db.add(org)
        db.flush()
        out = seed_into_org(db, org, days=days, n_users=n_users, seed_val=seed_val)
        out["api_key"] = raw_key
        return out
    finally:
        db.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Seed a synthetic ValueLedger org.")
    ap.add_argument("--org", default="Northwind Financial")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--users", type=int, default=40)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    out = seed(a.org, a.days, a.users, a.seed)
    print("\n  Seeded ValueLedger\n")
    for k, v in out.items():
        print(f"    {k:>16}: {v}")
    print("\n  Save the API key — it is not recoverable.\n")


if __name__ == "__main__":
    sys.exit(main())
