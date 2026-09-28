"""Policy distribution — what the collector and skill fetch before classifying."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import schemas
from app.auth import Principal, require_admin, require_org
from app.db import get_db
from app.models import Org
from app.policy import (
    build_bundle, bundle_checksum, set_extraction_guidance, set_global_guidance,
    validate_bundle,
)

router = APIRouter(prefix="/v1", tags=["policy"])


@router.get("/policy", response_model=schemas.PolicyBundle)
def get_policy(org: Org = Depends(require_org), db: Session = Depends(get_db)):
    return build_bundle(db, org.id)


@router.get("/policy/status")
def policy_status(org: Org = Depends(require_org), db: Session = Depends(get_db)):
    """Bundle health — surfaced in the admin UI so bad data entry is caught early."""
    bundle = build_bundle(db, org.id)
    problems = validate_bundle(bundle)
    return {
        "policy_version": bundle.policy_version,
        "checksum": bundle_checksum(bundle),
        "counts": {
            "initiatives": len(bundle.initiatives),
            "task_types": len(bundle.task_types),
            "activities": len(bundle.activities),
            "examples": len(bundle.examples),
            "baselines": len(bundle.baselines),
        },
        "ready": not problems,
        "problems": problems,
    }


@router.put("/policy", response_model=schemas.PolicyBundle)
def update_policy(body: schemas.PolicyUpdate, p: Principal = Depends(require_admin),
                  db: Session = Depends(get_db)):
    current = build_bundle(db, p.org.id)
    if body.global_guidance is not None and \
            body.global_guidance.strip() != (current.global_guidance or "").strip():
        set_global_guidance(db, p.org.id, body.global_guidance, p.email)
    if body.output_extraction_guidance is not None and \
            body.output_extraction_guidance.strip() != \
            (current.output_extraction_guidance or "").strip():
        set_extraction_guidance(db, p.org.id, body.output_extraction_guidance, p.email)
    db.commit()
    return build_bundle(db, p.org.id)
