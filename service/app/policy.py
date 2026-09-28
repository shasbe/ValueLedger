"""Attribution Policy — the versioned prompt bundle (SPEC 5.1).

The service is the single source of truth for *how to classify*, not just for the
list of labels. Every dimension carries its own prompt text, and the whole bundle
is versioned so a label can be traced to the exact prompts that produced it.
"""
from __future__ import annotations

import hashlib
import json

from sqlalchemy.orm import Session

from app.models import (
    Activity, AttributionPolicy, Initiative, PolicyExample, ProductivityBaseline, TaskType,
)
from app.schemas import PolicyBaseline, PolicyBundle, PolicyDimension, PolicyExampleOut

DEFAULT_GLOBAL_GUIDANCE = (
    "You are classifying a completed Claude session against this organization's "
    "attribution taxonomy. You are given the user's typed prompts in order, plus "
    "repository and branch context.\n\n"
    "Judge the work that was actually done, not what was merely discussed. Weigh the "
    "first and last prompts most heavily: the first states intent, the last shows where "
    "the work landed.\n\n"
    "If no initiative in the taxonomy genuinely fits, return `unclassifiable` rather "
    "than forcing the closest match. A wrong label is worse than an absent one — "
    "unclassified work is a signal that the taxonomy is missing something."
)


DEFAULT_EXTRACTION_GUIDANCE = (
    "After classifying the session, count the units of work it actually produced, using "
    "the unit defined for the chosen task type in `baselines`.\n\n"
    "Count only work that was completed and kept in this session. Do not count drafts that "
    "were discarded, work the user rejected, or things merely discussed or planned. If the "
    "session produced nothing durable, return an empty list — that is a normal and useful "
    "answer, not a failure.\n\n"
    "Prefer undercounting to overcounting. These numbers feed a productivity estimate that "
    "a finance team will scrutinise, and an inflated count discredits every other number in "
    "the report. When genuinely unsure between two values, return the lower one."
)


def current_version(db: Session, org_id: str) -> int:
    row = (db.query(AttributionPolicy)
             .filter(AttributionPolicy.org_id == org_id)
             .order_by(AttributionPolicy.version.desc()).first())
    return row.version if row else 0


def bump_policy_version(db: Session, org_id: str, published_by: str | None) -> int:
    """Publish a new policy version. Called whenever taxonomy or guidance changes."""
    prev = (db.query(AttributionPolicy)
              .filter(AttributionPolicy.org_id == org_id)
              .order_by(AttributionPolicy.version.desc()).first())
    version = (prev.version + 1) if prev else 1
    db.add(AttributionPolicy(
        org_id=org_id, version=version, published_by=published_by,
        global_guidance=prev.global_guidance if prev else DEFAULT_GLOBAL_GUIDANCE,
        output_extraction_guidance=(prev.output_extraction_guidance if prev
                                    else DEFAULT_EXTRACTION_GUIDANCE),
    ))
    db.flush()
    return version


def set_global_guidance(db: Session, org_id: str, text: str, by: str | None) -> int:
    version = bump_policy_version(db, org_id, by)
    row = (db.query(AttributionPolicy)
             .filter(AttributionPolicy.org_id == org_id,
                     AttributionPolicy.version == version).first())
    row.global_guidance = text
    db.flush()
    return version


def set_extraction_guidance(db: Session, org_id: str, text: str, by: str | None) -> int:
    version = bump_policy_version(db, org_id, by)
    row = (db.query(AttributionPolicy)
             .filter(AttributionPolicy.org_id == org_id,
                     AttributionPolicy.version == version).first())
    row.output_extraction_guidance = text
    db.flush()
    return version


def build_bundle(db: Session, org_id: str) -> PolicyBundle:
    """Assemble the bundle the collector and skill fetch before classifying."""
    pol = (db.query(AttributionPolicy)
             .filter(AttributionPolicy.org_id == org_id)
             .order_by(AttributionPolicy.version.desc()).first())
    version = pol.version if pol else 0
    guidance = pol.global_guidance if pol else DEFAULT_GLOBAL_GUIDANCE

    def dims(model, extra_active_filter=False):
        q = db.query(model).filter(model.org_id == org_id)
        if extra_active_filter:
            q = q.filter(model.status == "active")
        return [PolicyDimension(key=r.key, name=r.name,
                                description=r.description or "",
                                classification_guidance=r.classification_guidance or "")
                for r in q.order_by(model.key).all()]

    examples = [
        PolicyExampleOut(dimension=e.dimension, example_text=e.example_text, label=e.label)
        for e in db.query(PolicyExample)
                   .filter(PolicyExample.org_id == org_id)
                   .order_by(PolicyExample.created_at.desc()).limit(50).all()
    ]

    baselines = [
        PolicyBaseline(
            task_type=b.task_type_key, unit=b.unit, unit_plural=b.unit_plural,
            human_minutes_per_unit=b.human_minutes_per_unit,
            range_minutes=([b.minutes_low, b.minutes_high]
                           if b.minutes_low is not None and b.minutes_high is not None
                           else None),
            source=b.source or "", source_type=b.source_type)
        for b in db.query(ProductivityBaseline)
                   .filter(ProductivityBaseline.org_id == org_id,
                           ProductivityBaseline.active == True)  # noqa: E712
                   .order_by(ProductivityBaseline.task_type_key).all()
    ]

    return PolicyBundle(
        policy_version=version,
        global_guidance=guidance,
        initiatives=dims(Initiative, extra_active_filter=True),
        task_types=dims(TaskType),
        activities=dims(Activity),
        examples=examples,
        output_extraction_guidance=(pol.output_extraction_guidance if pol
                                    else DEFAULT_EXTRACTION_GUIDANCE),
        baselines=baselines,
    )


def bundle_checksum(bundle: PolicyBundle) -> str:
    return hashlib.sha256(
        json.dumps(bundle.model_dump(), sort_keys=True).encode()
    ).hexdigest()[:16]


def validate_bundle(bundle: PolicyBundle) -> list[str]:
    """Problems that would make classification fail or produce garbage.

    Surfaced in the admin UI so data-entry mistakes are caught before the
    collector runs, rather than showing up as bad labels a week later.
    """
    problems: list[str] = []
    if not bundle.initiatives:
        problems.append("No active initiatives — every session will be unclassifiable.")
    if not bundle.task_types:
        problems.append("No task types defined.")
    if not bundle.activities:
        problems.append("No activities defined.")
    for dim_name, items in (("initiative", bundle.initiatives),
                            ("task type", bundle.task_types),
                            ("activity", bundle.activities)):
        for it in items:
            if not (it.classification_guidance or "").strip():
                problems.append(
                    f"{dim_name.capitalize()} '{it.key}' has no classification guidance — "
                    f"the classifier will have only its name to go on.")
    for b in bundle.baselines:
        if b.source_type in ("unset", "vendor_claim") or not (b.source or "").strip():
            problems.append(
                f"Baseline '{b.task_type}/{b.unit}' has no credible source "
                f"({b.source_type}) — productivity figures using it are illustrative only.")
    return problems
