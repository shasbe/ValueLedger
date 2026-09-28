"""Review queue and corrections — the flywheel.

A correction does two things: it fixes the record, and it is promoted into the
policy's few-shot examples so every client classifies better on the next fetch.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import schemas
from app.auth import Principal, require_principal, visible_initiative_ids
from app.db import get_db
from app.models import (
    Activity, Classification, ClassificationCorrection, Initiative, PolicyExample,
    SessionRow, TaskType,
)

router = APIRouter(prefix="/v1", tags=["review"])


@router.get("/review-queue")
def review_queue(p: Principal = Depends(require_principal), db: Session = Depends(get_db),
                 limit: int = 100):
    """Low-confidence and unclassifiable sessions, scoped to the caller."""
    q = (db.query(SessionRow, Classification)
           .join(Classification, Classification.session_pk == SessionRow.id)
           .filter(SessionRow.org_id == p.org.id,
                   Classification.status.in_(("needs_review", "unclassifiable"))))
    if not p.sees_all:
        if p.role == "initiative_owner":
            ids = visible_initiative_ids(db, p)
            # Unclassifiable rows have no initiative, so an owner would never see
            # them. They are the taxonomy-gap signal, so admins/finance see those.
            q = q.filter(Classification.initiative_id.in_(ids or ["__none__"]))
        else:
            q = q.filter(SessionRow.user_email == p.email)

    inits = {i.id: i.name for i in db.query(Initiative).filter(Initiative.org_id == p.org.id).all()}
    # A record with no start time (published over MCP, where the model may not
    # know when the conversation began) must not sort to the bottom and vanish.
    rows = (q.order_by(func.coalesce(SessionRow.started_at,
                                     SessionRow.created_at).desc())
             .limit(limit).all())
    return {
        "count": len(rows),
        "items": [{
            "classification_id": cl.id,
            "session_id": s.session_id,
            "surface": s.surface,
            "user_email": s.user_email,
            "repo": s.repo,
            "branch": s.branch,
            "started_at": s.started_at.isoformat() if s.started_at else None,
            "status": cl.status,
            "initiative": inits.get(cl.initiative_id),
            "confidence": cl.conf_initiative,
            # In `local` privacy mode the service never receives prompt text, so
            # the rationale is the only evidence a reviewer has (SPEC 5.3).
            "rationale": cl.rationale,
            "policy_version": cl.policy_version,
        } for s, cl in rows],
    }


@router.post("/classifications/{classification_id}/correct")
def correct(classification_id: str, body: schemas.CorrectionIn,
            p: Principal = Depends(require_principal), db: Session = Depends(get_db)):
    cl = db.query(Classification).filter(Classification.id == classification_id).first()
    if not cl:
        raise HTTPException(404, "Classification not found")
    s = db.query(SessionRow).filter(SessionRow.id == cl.session_pk).first()
    if not s or s.org_id != p.org.id:
        raise HTTPException(404, "Classification not found")

    # A member may correct their own rows. Owners and finance may correct any row
    # they can already see.
    if not p.sees_all and s.user_email.lower() != (p.email or "").lower():
        if p.role != "initiative_owner":
            raise HTTPException(403, "Not permitted to correct this record")
        if cl.initiative_id not in (visible_initiative_ids(db, p) or []):
            raise HTTPException(403, "Not permitted to correct this record")

    prev = {"initiative_id": cl.initiative_id, "task_type_id": cl.task_type_id,
            "activity_id": cl.activity_id, "status": cl.status}

    def _resolve(model, key):
        if not key:
            return None
        r = db.query(model).filter(model.org_id == p.org.id, model.key == key).first()
        if not r:
            raise HTTPException(400, f"Unknown key for {model.__tablename__}: {key}")
        return r

    init = _resolve(Initiative, body.initiative_key)
    tt = _resolve(TaskType, body.task_type_key)
    ac = _resolve(Activity, body.activity_key)

    if init:
        cl.initiative_id = init.id
    if tt:
        cl.task_type_id = tt.id
    if ac:
        cl.activity_id = ac.id
    cl.status = "corrected"
    # A human said so — confidence is no longer the model's to report.
    cl.conf_initiative = 1.0 if init else cl.conf_initiative
    db.flush()

    new = {"initiative_id": cl.initiative_id, "task_type_id": cl.task_type_id,
           "activity_id": cl.activity_id, "status": cl.status}
    corr = ClassificationCorrection(
        classification_id=cl.id, corrected_by=p.email or "system",
        prev_json=json.dumps(prev), new_json=json.dumps(new), note=body.note)
    db.add(corr)
    db.flush()

    # Promote to a few-shot example so the next policy fetch carries the lesson.
    promoted = 0
    if body.promote_example and body.note and init:
        db.add(PolicyExample(
            org_id=p.org.id, policy_version=cl.policy_version or 1,
            dimension="initiative", example_text=body.note.strip(),
            label=init.key, source_correction_id=corr.id))
        promoted = 1

    db.commit()
    return {"classification_id": cl.id, "status": cl.status,
            "correction_id": corr.id, "examples_promoted": promoted}
