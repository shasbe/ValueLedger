"""Ingest — the contract every collector and the MCP server writes through.

Two invariants:
  * Idempotent. Dedupe is on (session_id) and (session, record_uuid), so a
    re-run of `collect --full` can never double-count.
  * Cost is computed here from raw token counts. It is never accepted from a
    client, because a model-mediated caller could supply anything (SPEC 7).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import schemas
from app.auth import require_org
from app.db import get_db
from app.models import (
    Activity, Classification, Initiative, Org, OutcomeEvent, SessionRow, TaskType,
    UsageEvent, WorkOutput,
)
from app.pricing import normalize_model, price_usage_event

router = APIRouter(prefix="/v1", tags=["ingest"])

VALID_SURFACES = {"claude_code", "cowork", "chat"}
VALID_COST_BASIS = {"measured", "unknown"}
VALID_COLLECTION = {"collector", "mcp_skill"}
CONFIDENCE_FLOOR = 0.7


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _lookup(db: Session, model, org_id: str, key: str | None):
    if not key:
        return None
    return db.query(model).filter(model.org_id == org_id, model.key == key).first()


@router.post("/events", response_model=schemas.IngestResult)
def ingest(batch: schemas.EventsBatch, org: Org = Depends(require_org),
           db: Session = Depends(get_db)):
    res = schemas.IngestResult(
        sessions_received=len(batch.sessions), sessions_created=0, sessions_updated=0,
        usage_events_added=0, usage_events_deduped=0, outcomes_added=0, cost_usd_added=0.0,
    )
    unpriced: set[str] = set()

    for s in batch.sessions:
        if s.surface not in VALID_SURFACES:
            raise HTTPException(400, f"Invalid surface: {s.surface}")
        if s.cost_basis not in VALID_COST_BASIS:
            raise HTTPException(400, f"Invalid cost_basis: {s.cost_basis}")
        if s.collection_method not in VALID_COLLECTION:
            raise HTTPException(400, f"Invalid collection_method: {s.collection_method}")

        # Chat has no local transcript and the model cannot see its own usage, so a
        # chat session claiming measured cost is a bug or a spoof. Reject it.
        if s.surface == "chat" and s.cost_basis == "measured":
            raise HTTPException(400, "chat sessions cannot have cost_basis=measured")

        row = (db.query(SessionRow)
                 .filter(SessionRow.org_id == org.id,
                         SessionRow.session_id == s.session_id).first())
        if row is None:
            row = SessionRow(org_id=org.id, session_id=s.session_id)
            db.add(row)
            res.sessions_created += 1
        else:
            res.sessions_updated += 1

        row.surface = s.surface
        row.user_email = s.user_email.lower().strip()
        row.principal_type = s.principal_type
        row.parent_session_id = s.parent_session_id
        row.repo = s.repo
        row.branch = s.branch
        row.cwd_hash = s.cwd_hash
        row.client_version = s.client_version
        row.collection_method = s.collection_method
        row.cost_basis = s.cost_basis
        row.external_ref = s.external_ref
        started, ended = _aware(s.started_at), _aware(s.ended_at)
        # Widen the window rather than overwrite — a later batch may carry more of
        # the same session.
        if started and (row.started_at is None or started < _aware(row.started_at)):
            row.started_at = started
        if ended and (row.ended_at is None or ended > _aware(row.ended_at)):
            row.ended_at = ended
        db.flush()

        # ---- usage: dedupe on record_uuid ----
        existing = {
            u for (u,) in db.query(UsageEvent.record_uuid)
                            .filter(UsageEvent.session_pk == row.id).all()
        }
        for ue in s.usage:
            if ue.record_uuid in existing:
                res.usage_events_deduped += 1
                continue
            existing.add(ue.record_uuid)
            ev = UsageEvent(
                session_pk=row.id, record_uuid=ue.record_uuid, ts=_aware(ue.ts),
                model=normalize_model(ue.model), request_id=ue.request_id,
                input_tokens=ue.input_tokens, output_tokens=ue.output_tokens,
                cache_read_tokens=ue.cache_read_tokens,
                cache_write_5m_tokens=ue.cache_write_5m_tokens,
                cache_write_1h_tokens=ue.cache_write_1h_tokens,
                web_search_requests=ue.web_search_requests,
            )
            price_usage_event(db, ev)
            if not ev.priced:
                unpriced.add(ev.model)
            db.add(ev)
            res.usage_events_added += 1
            res.cost_usd_added += ev.cost_usd or 0.0

        # ---- outcomes: dedupe on (type, ref) ----
        seen = {
            (t, r) for (t, r) in db.query(OutcomeEvent.type, OutcomeEvent.ref)
                                   .filter(OutcomeEvent.session_pk == row.id).all()
        }
        for oc in s.outcomes:
            if (oc.type, oc.ref) in seen:
                continue
            seen.add((oc.type, oc.ref))
            db.add(OutcomeEvent(
                org_id=org.id, session_pk=row.id, type=oc.type, ref=oc.ref,
                ts=_aware(oc.ts),
                metadata_json=json.dumps(oc.metadata) if oc.metadata else None))
            res.outcomes_added += 1

        # ---- classification (may arrive with the session or later) ----
        if s.classification:
            _apply_classification(db, org.id, row, s.classification)

    db.commit()
    res.unpriced_models = sorted(unpriced)
    return res


def _apply_classification(db: Session, org_id: str, row: SessionRow,
                          c: schemas.ClassificationIn) -> Classification:
    cl = db.query(Classification).filter(Classification.session_pk == row.id).first()
    if cl is None:
        cl = Classification(session_pk=row.id)
        db.add(cl)

    init = _lookup(db, Initiative, org_id, c.initiative_key)
    tt = _lookup(db, TaskType, org_id, c.task_type_key)
    ac = _lookup(db, Activity, org_id, c.activity_key)

    # A label naming something outside the policy is dropped, not created. The
    # classifier may only choose from the taxonomy an admin authored.
    cl.initiative_id = init.id if init else None
    cl.task_type_id = tt.id if tt else None
    cl.activity_id = ac.id if ac else None
    cl.conf_initiative = c.conf_initiative
    cl.conf_task_type = c.conf_task_type
    cl.conf_activity = c.conf_activity
    cl.rationale = c.rationale
    cl.classifier_model = c.classifier_model
    cl.classifier_cost_usd = c.classifier_cost_usd or 0.0
    cl.classifier_version = c.classifier_version
    cl.policy_version = c.policy_version

    if c.status:
        cl.status = c.status
    else:
        confs = [x for x in (c.conf_initiative, c.conf_task_type, c.conf_activity)
                 if x is not None]
        if cl.initiative_id is None:
            cl.status = "unclassifiable"
        elif confs and min(confs) < CONFIDENCE_FLOOR:
            cl.status = "needs_review"
        else:
            cl.status = "auto"
    # Work units — the measured numerator for productivity. Replaced wholesale on
    # re-classification so a re-run cannot accumulate duplicate counts.
    if c.work_units:
        db.query(WorkOutput).filter(WorkOutput.session_pk == row.id).delete(
            synchronize_session=False)
        for w in c.work_units:
            if w.count <= 0:
                continue
            db.add(WorkOutput(session_pk=row.id, unit=w.unit, count=float(w.count),
                              basis=("measured" if w.basis == "measured"
                                     else "model_extracted"),
                              detail=w.detail))
    db.flush()
    return cl


@router.post("/sessions/{session_id}/outcomes")
def add_outcomes(session_id: str, outcomes: list[schemas.OutcomeIn],
                 org: Org = Depends(require_org), db: Session = Depends(get_db)):
    row = (db.query(SessionRow)
             .filter(SessionRow.org_id == org.id,
                     SessionRow.session_id == session_id).first())
    if not row:
        raise HTTPException(404, "Session not found")
    seen = {
        (t, r) for (t, r) in db.query(OutcomeEvent.type, OutcomeEvent.ref)
                               .filter(OutcomeEvent.session_pk == row.id).all()
    }
    added = 0
    for oc in outcomes:
        if (oc.type, oc.ref) in seen:
            continue
        seen.add((oc.type, oc.ref))
        db.add(OutcomeEvent(org_id=org.id, session_pk=row.id, type=oc.type, ref=oc.ref,
                            ts=_aware(oc.ts),
                            metadata_json=json.dumps(oc.metadata) if oc.metadata else None))
        added += 1
    db.commit()
    return {"session_id": session_id, "outcomes_added": added}
