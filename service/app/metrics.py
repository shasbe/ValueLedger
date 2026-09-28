"""Ledger aggregation.

The metrics here are the point of the product: cost and outcome joined at session
grain. Two rules hold throughout:

  * No dollar value of work produced is ever computed. We report attributed cost
    and verifiable output counts, nothing modelled (SPEC 3).
  * Coverage is always two numbers — classification and cost — never collapsed
    into one, because chat can be classified but not priced.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Activity, Classification, Initiative, OutcomeEvent, SessionRow, TaskType, UsageEvent,
)

# Outcome types that count as a durable artifact for the dark-spend calculation.
DURABLE = ("commit", "pr_opened", "pr_merged", "test_added", "file_changed")

# Sessions cheaper than this are excluded from dark spend — a 4-cent session that
# produced nothing is noise, not waste.
DARK_SPEND_FLOOR_USD = 0.25


@dataclass
class Filters:
    org_id: str
    start: datetime | None = None
    end: datetime | None = None
    surface: str | None = None
    initiative_ids: list[str] | None = None   # None = all
    user_email: str | None = None
    repo: str | None = None
    extra_session_ids: list[str] | None = field(default=None)


def _session_cost_subq(db: Session):
    """Per-session cost, rolled up from request-grain usage events."""
    return (
        select(UsageEvent.session_pk.label("spk"),
               func.coalesce(func.sum(UsageEvent.cost_usd), 0.0).label("cost"))
        .group_by(UsageEvent.session_pk).subquery()
    )


def _outcome_count_subq(db: Session, types: tuple[str, ...] | None = None):
    q = select(OutcomeEvent.session_pk.label("spk"),
               func.count(OutcomeEvent.id).label("n"))
    if types:
        q = q.where(OutcomeEvent.type.in_(types))
    return q.group_by(OutcomeEvent.session_pk).subquery()


def base_query(db: Session, f: Filters):
    """Sessions joined to cost and classification, with all filters applied."""
    cost = _session_cost_subq(db)
    q = (db.query(SessionRow, cost.c.cost, Classification)
           .outerjoin(cost, cost.c.spk == SessionRow.id)
           .outerjoin(Classification, Classification.session_pk == SessionRow.id)
           .filter(SessionRow.org_id == f.org_id))
    if f.start:
        q = q.filter(SessionRow.started_at >= f.start)
    if f.end:
        q = q.filter(SessionRow.started_at <= f.end)
    if f.surface:
        q = q.filter(SessionRow.surface == f.surface)
    if f.user_email:
        q = q.filter(SessionRow.user_email == f.user_email.lower())
    if f.repo:
        q = q.filter(SessionRow.repo == f.repo)
    if f.initiative_ids is not None:
        if not f.initiative_ids:
            q = q.filter(False)
        else:
            q = q.filter(Classification.initiative_id.in_(f.initiative_ids))
    return q


def summary(db: Session, f: Filters) -> dict:
    """Headline numbers for any scope."""
    rows = base_query(db, f).all()
    session_pks = [s.id for s, _c, _cl in rows]

    total_cost = sum((c or 0.0) for _s, c, _cl in rows)
    n_sessions = len(rows)
    measured = [(s, c, cl) for s, c, cl in rows if s.cost_basis == "measured"]
    classified = [(s, c, cl) for s, c, cl in rows
                  if cl and cl.initiative_id and cl.status != "unclassifiable"]

    # Outcomes across the scope.
    outcome_counts: dict[str, int] = {}
    if session_pks:
        for t, n in (db.query(OutcomeEvent.type, func.count(OutcomeEvent.id))
                       .filter(OutcomeEvent.session_pk.in_(session_pks))
                       .group_by(OutcomeEvent.type).all()):
            outcome_counts[t] = n

    # Dark spend: cost on sessions above the floor with no durable artifact.
    with_durable = set()
    if session_pks:
        with_durable = {
            spk for (spk,) in db.query(OutcomeEvent.session_pk)
                                .filter(OutcomeEvent.session_pk.in_(session_pks),
                                        OutcomeEvent.type.in_(DURABLE)).distinct().all()
        }
    dark = sum((c or 0.0) for s, c, _cl in rows
               if s.id not in with_durable and (c or 0.0) >= DARK_SPEND_FLOOR_USD)

    # Rework: cost on sessions that were later reverted.
    reverted = set()
    if session_pks:
        reverted = {
            spk for (spk,) in db.query(OutcomeEvent.session_pk)
                                .filter(OutcomeEvent.session_pk.in_(session_pks),
                                        OutcomeEvent.type == "revert").distinct().all()
        }
    rework = sum((c or 0.0) for s, c, _cl in rows if s.id in reverted)

    classifier_cost = sum((cl.classifier_cost_usd or 0.0) for _s, _c, cl in rows if cl)
    merged = outcome_counts.get("pr_merged", 0)

    return {
        "sessions": n_sessions,
        "attributed_spend_usd": round(total_cost, 4),
        "outcomes": outcome_counts,
        "merged_prs": merged,
        # Explicitly null rather than 0 when nothing merged — a cost-per-PR of
        # zero would read as free work.
        "cost_per_merged_pr_usd": round(total_cost / merged, 2) if merged else None,
        "dark_spend_usd": round(dark, 4),
        "dark_spend_pct": round(100 * dark / total_cost, 1) if total_cost else 0.0,
        "rework_spend_usd": round(rework, 4),
        "rework_spend_pct": round(100 * rework / total_cost, 1) if total_cost else 0.0,
        "coverage": {
            "classification_pct": round(100 * len(classified) / n_sessions, 1) if n_sessions else 0.0,
            "cost_pct": round(100 * len(measured) / n_sessions, 1) if n_sessions else 0.0,
            "classified_sessions": len(classified),
            "measured_sessions": len(measured),
            "unclassified_sessions": n_sessions - len(classified),
        },
        "classification_overhead_usd": round(classifier_cost, 4),
        "classification_overhead_pct": (
            round(100 * classifier_cost / total_cost, 2) if total_cost else 0.0),
    }


_GROUPERS = {
    "initiative": (Initiative, Classification.initiative_id),
    "task_type": (TaskType, Classification.task_type_id),
    "activity": (Activity, Classification.activity_id),
}


def rollup(db: Session, f: Filters, group_by: str) -> list[dict]:
    """Spend and outcomes grouped by one dimension.

    `cost_center` is not handled here — it lives on the user directory rather than
    the session, so it routes to cost_center_rollup().
    """
    rows = base_query(db, f).all()
    session_pks = [s.id for s, _c, _cl in rows]

    durable_by_session: dict[str, int] = {}
    merged_by_session: dict[str, int] = {}
    if session_pks:
        for spk, n in (db.query(OutcomeEvent.session_pk, func.count(OutcomeEvent.id))
                         .filter(OutcomeEvent.session_pk.in_(session_pks),
                                 OutcomeEvent.type.in_(DURABLE))
                         .group_by(OutcomeEvent.session_pk).all()):
            durable_by_session[spk] = n
        for spk, n in (db.query(OutcomeEvent.session_pk, func.count(OutcomeEvent.id))
                         .filter(OutcomeEvent.session_pk.in_(session_pks),
                                 OutcomeEvent.type == "pr_merged")
                         .group_by(OutcomeEvent.session_pk).all()):
            merged_by_session[spk] = n

    # Resolve dimension ids to labels in one pass.
    labels: dict[str, str] = {}
    if group_by in _GROUPERS:
        model, _col = _GROUPERS[group_by]
        labels = {r.id: r.name for r in db.query(model).filter(model.org_id == f.org_id).all()}

    buckets: dict[str, dict] = {}
    for s, c, cl in rows:
        if group_by == "user":
            key, label = s.user_email, s.user_email
        elif group_by == "surface":
            key, label = s.surface, s.surface
        elif group_by == "repo":
            key = label = s.repo or "(no repo)"
        elif group_by in _GROUPERS:
            dim_id = (getattr(cl, f"{group_by}_id", None) if cl else None)
            key = dim_id or "__unclassified__"
            label = labels.get(dim_id, "Unclassified") if dim_id else "Unclassified"
        else:
            key = label = "all"

        b = buckets.setdefault(key, {
            "key": key, "label": label, "sessions": 0, "spend_usd": 0.0,
            "merged_prs": 0, "dark_spend_usd": 0.0, "measured_sessions": 0,
        })
        cost = c or 0.0
        b["sessions"] += 1
        b["spend_usd"] += cost
        b["merged_prs"] += merged_by_session.get(s.id, 0)
        if s.cost_basis == "measured":
            b["measured_sessions"] += 1
        if s.id not in durable_by_session and cost >= DARK_SPEND_FLOOR_USD:
            b["dark_spend_usd"] += cost

    out = []
    for b in buckets.values():
        b["spend_usd"] = round(b["spend_usd"], 4)
        b["dark_spend_usd"] = round(b["dark_spend_usd"], 4)
        b["cost_per_merged_pr_usd"] = (
            round(b["spend_usd"] / b["merged_prs"], 2) if b["merged_prs"] else None)
        out.append(b)
    return sorted(out, key=lambda x: x["spend_usd"], reverse=True)


def cost_center_rollup(db: Session, f: Filters) -> list[dict]:
    """Cost centers come from the user directory, so this joins through users."""
    from app.models import User
    rows = base_query(db, f).all()
    cc = {u.email: (u.cost_center or "(unassigned)")
          for u in db.query(User).filter(User.org_id == f.org_id).all()}
    buckets: dict[str, dict] = {}
    for s, c, _cl in rows:
        key = cc.get(s.user_email, "(unassigned)")
        b = buckets.setdefault(key, {"key": key, "label": key, "sessions": 0, "spend_usd": 0.0})
        b["sessions"] += 1
        b["spend_usd"] += (c or 0.0)
    for b in buckets.values():
        b["spend_usd"] = round(b["spend_usd"], 4)
    return sorted(buckets.values(), key=lambda x: x["spend_usd"], reverse=True)


def daily_trend(db: Session, f: Filters) -> list[dict]:
    rows = base_query(db, f).all()
    by_day: dict[str, float] = {}
    for s, c, _cl in rows:
        if not s.started_at:
            continue
        day = s.started_at.strftime("%Y-%m-%d")
        by_day[day] = by_day.get(day, 0.0) + (c or 0.0)
    return [{"day": d, "spend_usd": round(v, 4)} for d, v in sorted(by_day.items())]
