"""Productivity gain — the one modelled metric in the product.

Everything else ValueLedger reports is measured. This is not, and the module is
built so that can never be forgotten:

  * The **numerator** (units of work produced, and actual human time spent) is
    measured from transcripts, git, and session timestamps.
  * The **denominator** (what that work would have cost a human) is an
    *assumption the customer owns* — a versioned baseline with a named source.

Every result therefore ships with `assumptions`, a `sensitivity` band, and a
`modelled_coverage` figure saying how much of the scope the estimate even covers.
A bare speedup number is never returned, because a bare speedup number is the
thing that gets the whole product disbelieved.

Two deliberate conservatisms:
  * **Only accepted work counts.** A session whose output was reverted
    contributes its cost and zero productivity.
  * **Human time is capped per session** (org.session_minutes_cap), because
    wall-clock overstates attention — people walk away mid-session.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.metrics import Filters, base_query
from app.models import (
    Org, OutcomeEvent, ProductivityBaseline, SessionRow, TaskType, WorkOutput,
)

# Source types ordered by how much weight a reader should give them.
SOURCE_TRUST = {
    "customer_measured": "Measured by the customer",
    "team_survey": "Self-reported by the team",
    "industry_estimate": "External/industry estimate",
    "vendor_claim": "Vendor-supplied — treat with caution",
    "unset": "No source recorded — not credible",
}


@dataclass
class BaselineUse:
    task_type_key: str
    unit: str
    minutes: float
    minutes_low: float
    minutes_high: float
    source: str
    source_type: str
    units_counted: float
    measured_units: float
    extracted_units: float


def _session_minutes(s: SessionRow, cap: int) -> float:
    if not s.started_at or not s.ended_at:
        return 0.0
    mins = (s.ended_at - s.started_at).total_seconds() / 60.0
    if mins <= 0:
        return 0.0
    return min(mins, float(cap))


def compute(db: Session, f: Filters) -> dict:
    """Productivity for a scope, with its assumptions attached."""
    org = db.query(Org).filter(Org.id == f.org_id).first()
    cap = org.session_minutes_cap if org else 90

    rows = base_query(db, f).all()
    session_pks = [s.id for s, _c, _cl in rows]
    if not session_pks:
        return _empty(cap)

    # Sessions whose work was later reverted earn no productivity credit.
    reverted = {
        spk for (spk,) in db.query(OutcomeEvent.session_pk)
                            .filter(OutcomeEvent.session_pk.in_(session_pks),
                                    OutcomeEvent.type == "revert").distinct().all()
    }

    outputs: dict[str, list[WorkOutput]] = {}
    for w in db.query(WorkOutput).filter(WorkOutput.session_pk.in_(session_pks)).all():
        outputs.setdefault(w.session_pk, []).append(w)

    task_name = {t.id: t.key for t in db.query(TaskType).filter(TaskType.org_id == f.org_id).all()}
    baselines = {
        (b.task_type_key, b.unit): b
        for b in db.query(ProductivityBaseline)
                   .filter(ProductivityBaseline.org_id == f.org_id,
                           ProductivityBaseline.active == True).all()  # noqa: E712
    }

    used: dict[tuple[str, str], BaselineUse] = {}
    human_equiv = human_low = human_high = 0.0
    actual_minutes = 0.0
    covered_sessions = 0
    total_sessions = len(rows)
    sessions_with_output = 0
    excluded_reverted = 0

    for s, _cost, cl in rows:
        mins = _session_minutes(s, cap)
        ws = outputs.get(s.id, [])
        if ws:
            sessions_with_output += 1
        if s.id in reverted:
            excluded_reverted += 1
            actual_minutes += mins      # the time was still spent
            continue

        tt_key = task_name.get(cl.task_type_id) if cl else None
        matched = False
        for w in ws:
            b = baselines.get((tt_key, w.unit)) if tt_key else None
            if b is None:
                continue
            matched = True
            lo = b.minutes_low if b.minutes_low is not None else b.human_minutes_per_unit
            hi = b.minutes_high if b.minutes_high is not None else b.human_minutes_per_unit
            human_equiv += w.count * b.human_minutes_per_unit
            human_low += w.count * lo
            human_high += w.count * hi

            k = (tt_key, w.unit)
            u = used.get(k)
            if u is None:
                u = BaselineUse(task_type_key=tt_key, unit=w.unit,
                                minutes=b.human_minutes_per_unit, minutes_low=lo,
                                minutes_high=hi, source=b.source or "",
                                source_type=b.source_type, units_counted=0.0,
                                measured_units=0.0, extracted_units=0.0)
                used[k] = u
            u.units_counted += w.count
            if w.basis == "measured":
                u.measured_units += w.count
            else:
                u.extracted_units += w.count

        actual_minutes += mins
        if matched:
            covered_sessions += 1

    def ratio(h: float) -> float | None:
        return round(h / actual_minutes, 2) if actual_minutes > 0 else None

    total_units = sum(u.units_counted for u in used.values())
    measured_units = sum(u.measured_units for u in used.values())

    return {
        "measured": {
            "actual_human_hours": round(actual_minutes / 60.0, 1),
            "sessions_in_scope": total_sessions,
            "sessions_with_output": sessions_with_output,
            "units_produced": round(total_units, 1),
            "units_measured_pct": (round(100 * measured_units / total_units, 1)
                                   if total_units else 0.0),
            "session_minutes_cap": cap,
        },
        "modelled": {
            "human_equivalent_hours": round(human_equiv / 60.0, 1),
            "hours_saved": round((human_equiv - actual_minutes) / 60.0, 1),
            "speedup_x": ratio(human_equiv),
        },
        "sensitivity": {
            "speedup_low": ratio(human_low),
            "speedup_high": ratio(human_high),
            "hours_saved_low": round((human_low - actual_minutes) / 60.0, 1),
            "hours_saved_high": round((human_high - actual_minutes) / 60.0, 1),
        },
        "modelled_coverage": {
            "sessions_covered": covered_sessions,
            "coverage_pct": (round(100 * covered_sessions / total_sessions, 1)
                             if total_sessions else 0.0),
            "sessions_excluded_reverted": excluded_reverted,
        },
        "assumptions": sorted(
            [{
                "task_type": u.task_type_key,
                "unit": u.unit,
                "human_minutes_per_unit": u.minutes,
                "range_minutes": [u.minutes_low, u.minutes_high],
                "source": u.source,
                "source_type": u.source_type,
                "source_trust": SOURCE_TRUST.get(u.source_type, u.source_type),
                "units_counted": round(u.units_counted, 1),
                "units_measured": round(u.measured_units, 1),
                "units_model_extracted": round(u.extracted_units, 1),
            } for u in used.values()],
            key=lambda a: -a["units_counted"]),
        "caveats": _caveats(used, covered_sessions, total_sessions, cap),
    }


def _caveats(used, covered: int, total: int, cap: int) -> list[str]:
    """Stated in-product, not buried in docs. If the estimate is weak, it says so."""
    out: list[str] = []
    if total and covered / total < 0.5:
        out.append(
            f"Only {round(100 * covered / total)}% of sessions in scope have both a counted "
            f"output and a matching baseline. The speedup describes that subset, not the whole.")
    weak = [u for u in used.values() if u.source_type in ("unset", "vendor_claim")]
    if weak:
        names = ", ".join(sorted({f"{u.task_type_key}/{u.unit}" for u in weak}))
        out.append(f"Baselines without a credible source are in use ({names}). "
                   f"Treat the result as illustrative until a real one is recorded.")
    extracted = sum(u.extracted_units for u in used.values())
    total_u = sum(u.units_counted for u in used.values())
    if total_u and extracted / total_u > 0.5:
        out.append(
            f"{round(100 * extracted / total_u)}% of counted units were extracted by the "
            f"classifier from the transcript rather than measured from git or outcomes.")
    out.append(
        f"Human time is session wall-clock capped at {cap} min/session; it overstates "
        f"attention where a session was left open.")
    out.append(
        "Hours saved is not converted to dollars. Multiplying by a loaded rate would add "
        "a second assumption on top of the baseline.")
    return out


def _empty(cap: int) -> dict:
    return {
        "measured": {"actual_human_hours": 0.0, "sessions_in_scope": 0,
                     "sessions_with_output": 0, "units_produced": 0.0,
                     "units_measured_pct": 0.0, "session_minutes_cap": cap},
        "modelled": {"human_equivalent_hours": 0.0, "hours_saved": 0.0, "speedup_x": None},
        "sensitivity": {"speedup_low": None, "speedup_high": None,
                        "hours_saved_low": 0.0, "hours_saved_high": 0.0},
        "modelled_coverage": {"sessions_covered": 0, "coverage_pct": 0.0,
                              "sessions_excluded_reverted": 0},
        "assumptions": [], "caveats": ["No sessions in scope."],
    }
