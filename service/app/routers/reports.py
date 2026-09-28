"""Ledger queries and the three role-scoped reports.

Scoping is enforced here, in the query layer — never in the UI. A `member` can
only ever read rows where user_email is their own.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import Principal, assert_can_read_user, require_principal, visible_initiative_ids
from app.db import get_db
from app.metrics import Filters, base_query, cost_center_rollup, daily_trend, rollup, summary
from app.productivity import compute as productivity_compute
from app.models import Budget, Classification, Initiative, OutcomeEvent, SessionRow

router = APIRouter(prefix="/v1", tags=["reports"])

GROUP_BYS = {"initiative", "task_type", "activity", "user", "surface", "repo", "cost_center"}


def _parse(d: str | None) -> datetime | None:
    if not d:
        return None
    try:
        return datetime.fromisoformat(d).replace(tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(400, f"Bad date: {d} (expected ISO, e.g. 2026-07-01)")


@router.get("/ledger")
def get_ledger(p: Principal = Depends(require_principal), db: Session = Depends(get_db),
               start: str | None = None, end: str | None = None,
               surface: str | None = None, repo: str | None = None,
               user_email: str | None = None, limit: int = Query(200, le=2000)):
    f = Filters(org_id=p.org.id, start=_parse(start), end=_parse(end),
                surface=surface, repo=repo)
    if p.sees_all:
        if user_email:
            f.user_email = user_email
    elif p.role == "initiative_owner":
        f.initiative_ids = visible_initiative_ids(db, p)
    else:
        f.user_email = p.email

    rows = (base_query(db, f)
            .order_by(func.coalesce(SessionRow.started_at,
                                    SessionRow.created_at).desc())
            .limit(limit).all())
    inits = {i.id: i for i in db.query(Initiative).filter(Initiative.org_id == p.org.id).all()}
    out = []
    for s, c, cl in rows:
        out.append({
            "session_id": s.session_id,
            "surface": s.surface,
            "user_email": s.user_email,
            "repo": s.repo,
            "branch": s.branch,
            "started_at": s.started_at.isoformat() if s.started_at else None,
            "cost_usd": round(c or 0.0, 4),
            "cost_basis": s.cost_basis,
            "collection_method": s.collection_method,
            "initiative": inits[cl.initiative_id].name if (cl and cl.initiative_id in inits) else None,
            "status": cl.status if cl else None,
            "confidence": cl.conf_initiative if cl else None,
        })
    return {"count": len(out), "sessions": out}


@router.get("/rollup")
def get_rollup(group_by: str = Query("initiative"),
               p: Principal = Depends(require_principal), db: Session = Depends(get_db),
               start: str | None = None, end: str | None = None,
               surface: str | None = None):
    if group_by not in GROUP_BYS:
        raise HTTPException(400, f"group_by must be one of {sorted(GROUP_BYS)}")
    f = Filters(org_id=p.org.id, start=_parse(start), end=_parse(end), surface=surface)
    if not p.sees_all:
        if p.role == "initiative_owner":
            f.initiative_ids = visible_initiative_ids(db, p)
        else:
            f.user_email = p.email
    if group_by == "cost_center":
        return {"group_by": group_by, "rows": cost_center_rollup(db, f)}
    return {"group_by": group_by, "rows": rollup(db, f, group_by)}


@router.get("/reports/executive")
def executive(p: Principal = Depends(require_principal), db: Session = Depends(get_db),
              start: str | None = None, end: str | None = None):
    if not p.sees_all:
        raise HTTPException(403, "Finance or admin role required")
    f = Filters(org_id=p.org.id, start=_parse(start), end=_parse(end))
    return {
        "scope": "organization",
        "summary": summary(db, f),
        "by_initiative": rollup(db, f, "initiative"),
        "by_cost_center": cost_center_rollup(db, f),
        "by_surface": rollup(db, f, "surface"),
        "trend": daily_trend(db, f),
        "productivity": productivity_compute(db, f),
    }


@router.get("/reports/initiative/{key}")
def initiative_report(key: str, p: Principal = Depends(require_principal),
                      db: Session = Depends(get_db),
                      start: str | None = None, end: str | None = None):
    init = (db.query(Initiative)
              .filter(Initiative.org_id == p.org.id, Initiative.key == key).first())
    if not init:
        raise HTTPException(404, f"Initiative not found: {key}")
    if not p.sees_all and not (p.role == "initiative_owner"
                               and init.owner_email
                               and p.email
                               and init.owner_email.lower() == p.email.lower()):
        raise HTTPException(403, "Not permitted to read this initiative")

    f = Filters(org_id=p.org.id, start=_parse(start), end=_parse(end),
                initiative_ids=[init.id])
    s = summary(db, f)
    budget = (db.query(Budget)
                .filter(Budget.org_id == p.org.id, Budget.scope_type == "initiative",
                        Budget.scope_id == init.id).first())
    amount = budget.amount_usd if budget else init.budget_amount
    return {
        "initiative": {"key": init.key, "name": init.name, "owner_email": init.owner_email,
                       "cost_center": init.cost_center, "description": init.description},
        "budget": {
            "amount_usd": amount,
            "period": (budget.period if budget else init.budget_period),
            "consumed_pct": (round(100 * s["attributed_spend_usd"] / amount, 1)
                             if amount else None),
        },
        "summary": s,
        "by_task_type": rollup(db, f, "task_type"),
        "by_activity": rollup(db, f, "activity"),
        "by_user": rollup(db, f, "user"),
        "trend": daily_trend(db, f),
        "productivity": productivity_compute(db, f),
    }


@router.get("/reports/productivity")
def productivity_report(p: Principal = Depends(require_principal),
                        db: Session = Depends(get_db),
                        start: str | None = None, end: str | None = None,
                        task_type: str | None = None):
    """Productivity on its own, so the assumptions are unavoidable rather than a
    footnote under a headline number."""
    f = Filters(org_id=p.org.id, start=_parse(start), end=_parse(end))
    if not p.sees_all:
        if p.role == "initiative_owner":
            f.initiative_ids = visible_initiative_ids(db, p)
        else:
            f.user_email = p.email
    out = productivity_compute(db, f)
    out["by_task_type"] = rollup(db, f, "task_type")
    return out


@router.get("/reports/me")
def me_report(p: Principal = Depends(require_principal), db: Session = Depends(get_db),
              start: str | None = None, end: str | None = None):
    if not p.email:
        raise HTTPException(400, "No acting user — send X-User-Email")
    assert_can_read_user(p, p.email)
    f = Filters(org_id=p.org.id, start=_parse(start), end=_parse(end), user_email=p.email)
    return {
        "user_email": p.email,
        "role": p.role,
        "summary": summary(db, f),
        "by_initiative": rollup(db, f, "initiative"),
        "by_task_type": rollup(db, f, "task_type"),
        "trend": daily_trend(db, f),
        "productivity": productivity_compute(db, f),
    }
