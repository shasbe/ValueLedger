"""Org setup and taxonomy CRUD — what the admin UI writes to."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import schemas
from app.auth import Principal, hash_key, new_api_key, require_admin, require_org
from app.db import get_db
from app.models import (
    Activity, Budget, Initiative, McpToken, Org, PriceBook, ProductivityBaseline,
    TaskType, User,
)
from app.mcp import hash_token as hash_mcp_token, new_mcp_token
from app.policy import bump_policy_version

router = APIRouter(prefix="/v1", tags=["admin"])


def _snapshot(obj, fields) -> tuple:
    return tuple(getattr(obj, f, None) for f in fields)


def _bump_if_changed(db: Session, org_id: str, email: str | None,
                     before: tuple, after: tuple) -> None:
    """Publish a new policy version only when something actually changed.

    Re-applying an unchanged config file must not mint versions: policy_version
    is how a classification is traced back to the prompts that produced it, so a
    version that means nothing makes the audit trail worthless.
    """
    if before != after:
        bump_policy_version(db, org_id, email)


# ---------------- org bootstrap (unauthenticated: creates the first key) ----

@router.post("/orgs", response_model=schemas.OrgOut)
def create_org(body: schemas.OrgCreate, db: Session = Depends(get_db)):
    raw = new_api_key()
    org = Org(name=body.name, api_key_hash=hash_key(raw), privacy_mode="local")
    db.add(org)
    db.flush()
    # First user is the admin who created the org.
    db.add(User(org_id=org.id, email=f"admin@{org.name.lower().replace(' ', '')}.local",
                display_name="Admin", role="admin"))
    db.commit()
    return schemas.OrgOut(id=org.id, name=org.name, privacy_mode=org.privacy_mode,
                          session_minutes_cap=org.session_minutes_cap, api_key=raw)


@router.get("/demo-info")
def demo_info(db: Session = Depends(get_db)):
    """Credentials for a public demo instance.

    Only responds when DEMO_MODE is set and the org was created by the bootstrap
    seeder — so it can never leak a real customer's key. The data behind it is
    synthetic.
    """
    import os
    if os.environ.get("DEMO_MODE", "").lower() not in ("1", "true", "yes"):
        raise HTTPException(404, "Not a demo deployment")
    api_key = os.environ.get("BOOTSTRAP_API_KEY")
    if not api_key:
        raise HTTPException(404, "No demo org configured")
    org = db.query(Org).filter(Org.api_key_hash == hash_key(api_key)).first()
    if not org:
        raise HTTPException(404, "Demo org not present")

    def pick(role):
        u = db.query(User).filter(User.org_id == org.id, User.role == role).first()
        return u.email if u else None

    owner = db.query(User).filter(User.org_id == org.id,
                                  User.role == "initiative_owner").first()
    owned = None
    if owner:
        i = db.query(Initiative).filter(Initiative.org_id == org.id,
                                        Initiative.owner_email == owner.email).first()
        owned = i.key if i else None
    return {
        "org_name": org.name,
        "api_key": api_key,
        "roles": {
            "finance": pick("finance"),
            "admin": pick("admin"),
            "initiative_owner": owner.email if owner else None,
            "member": pick("member"),
        },
        "owned_initiative": owned,
        "synthetic": True,
    }


@router.post("/orgs/{org_id}/seed-demo")
def seed_demo(org_id: str, days: int = 90, p: Principal = Depends(require_admin),
              db: Session = Depends(get_db)):
    """Populate an org with synthetic data. Exists so a deployed instance can be
    filled without shell access; refuses if the org already holds sessions."""
    from app.models import SessionRow
    if p.org.id != org_id:
        raise HTTPException(403, "Can only seed your own org")
    if db.query(SessionRow).filter(SessionRow.org_id == org_id).count():
        raise HTTPException(400, "Org already has sessions — refusing to seed over them")
    from app.seed import seed_into_org
    out = seed_into_org(db, p.org, days=days)
    return out


@router.get("/org", response_model=schemas.OrgOut)
def get_org(org: Org = Depends(require_org)):
    return schemas.OrgOut(id=org.id, name=org.name, privacy_mode=org.privacy_mode,
                          session_minutes_cap=org.session_minutes_cap)


# ---------------- users ----------------

@router.get("/users", response_model=list[schemas.UserOut])
def list_users(org: Org = Depends(require_org), db: Session = Depends(get_db)):
    rows = db.query(User).filter(User.org_id == org.id).order_by(User.email).all()
    return [schemas.UserOut(id=u.id, email=u.email, display_name=u.display_name,
                            cost_center=u.cost_center, manager_email=u.manager_email,
                            role=u.role) for u in rows]


@router.post("/users", response_model=schemas.UserOut)
def upsert_user(body: schemas.UserIn, p: Principal = Depends(require_admin),
                db: Session = Depends(get_db)):
    if body.role not in ("member", "initiative_owner", "finance", "admin"):
        raise HTTPException(400, f"Invalid role: {body.role}")
    email = body.email.lower().strip()
    u = db.query(User).filter(User.org_id == p.org.id, User.email == email).first()
    if not u:
        u = User(org_id=p.org.id, email=email)
        db.add(u)
    u.display_name = body.display_name
    u.cost_center = body.cost_center
    u.manager_email = body.manager_email
    u.role = body.role
    db.commit()
    return schemas.UserOut(id=u.id, email=u.email, display_name=u.display_name,
                           cost_center=u.cost_center, manager_email=u.manager_email, role=u.role)


@router.delete("/users/{user_id}")
def delete_user(user_id: str, p: Principal = Depends(require_admin),
                db: Session = Depends(get_db)):
    u = db.query(User).filter(User.id == user_id, User.org_id == p.org.id).first()
    if not u:
        raise HTTPException(404, "User not found")
    db.delete(u)
    db.commit()
    return {"deleted": user_id}


# ---------------- initiatives ----------------

def _init_out(i: Initiative) -> schemas.InitiativeOut:
    return schemas.InitiativeOut(
        id=i.id, policy_version=i.policy_version, key=i.key, name=i.name,
        description=i.description or "", classification_guidance=i.classification_guidance or "",
        owner_email=i.owner_email, budget_amount=i.budget_amount,
        budget_period=i.budget_period, cost_center=i.cost_center, status=i.status)


@router.get("/initiatives", response_model=list[schemas.InitiativeOut])
def list_initiatives(org: Org = Depends(require_org), db: Session = Depends(get_db)):
    rows = db.query(Initiative).filter(Initiative.org_id == org.id).order_by(Initiative.key).all()
    return [_init_out(i) for i in rows]


@router.post("/initiatives", response_model=schemas.InitiativeOut)
def upsert_initiative(body: schemas.InitiativeIn, p: Principal = Depends(require_admin),
                      db: Session = Depends(get_db)):
    i = db.query(Initiative).filter(Initiative.org_id == p.org.id,
                                    Initiative.key == body.key).first()
    fields = ("name", "description", "classification_guidance", "owner_email",
              "budget_amount", "budget_period", "cost_center", "status")
    created = i is None
    if created:
        i = Initiative(org_id=p.org.id, key=body.key)
        db.add(i)
    before = None if created else _snapshot(i, fields)
    for f in fields:
        setattr(i, f, getattr(body, f))
    db.flush()
    after = _snapshot(i, fields)
    if created or before != after:
        i.policy_version = bump_policy_version(db, p.org.id, p.email)
    db.commit()
    return _init_out(i)


@router.delete("/initiatives/{initiative_id}")
def delete_initiative(initiative_id: str, p: Principal = Depends(require_admin),
                      db: Session = Depends(get_db)):
    i = db.query(Initiative).filter(Initiative.id == initiative_id,
                                    Initiative.org_id == p.org.id).first()
    if not i:
        raise HTTPException(404, "Initiative not found")
    db.delete(i)
    bump_policy_version(db, p.org.id, p.email)
    db.commit()
    return {"deleted": initiative_id}


# ---------------- task types / activities ----------------

def _dim_router(model, name: str):
    def list_items(org: Org = Depends(require_org), db: Session = Depends(get_db)):
        rows = db.query(model).filter(model.org_id == org.id).order_by(model.key).all()
        return [schemas.DimensionOut(id=r.id, policy_version=r.policy_version, key=r.key,
                                     name=r.name, description=r.description or "",
                                     classification_guidance=r.classification_guidance or "")
                for r in rows]

    def upsert_item(body: schemas.DimensionIn, p: Principal = Depends(require_admin),
                    db: Session = Depends(get_db)):
        r = db.query(model).filter(model.org_id == p.org.id, model.key == body.key).first()
        created = r is None
        if created:
            r = model(org_id=p.org.id, key=body.key)
            db.add(r)
        fields = ("name", "description", "classification_guidance")
        before = None if created else _snapshot(r, fields)
        r.name = body.name
        r.description = body.description
        r.classification_guidance = body.classification_guidance
        db.flush()
        if created or before != _snapshot(r, fields):
            r.policy_version = bump_policy_version(db, p.org.id, p.email)
        db.commit()
        return schemas.DimensionOut(id=r.id, policy_version=r.policy_version, key=r.key,
                                    name=r.name, description=r.description or "",
                                    classification_guidance=r.classification_guidance or "")

    def delete_item(item_id: str, p: Principal = Depends(require_admin),
                    db: Session = Depends(get_db)):
        r = db.query(model).filter(model.id == item_id, model.org_id == p.org.id).first()
        if not r:
            raise HTTPException(404, f"{name} not found")
        db.delete(r)
        bump_policy_version(db, p.org.id, p.email)
        db.commit()
        return {"deleted": item_id}

    return list_items, upsert_item, delete_item


_tt_list, _tt_upsert, _tt_delete = _dim_router(TaskType, "Task type")
router.get("/task-types", response_model=list[schemas.DimensionOut])(_tt_list)
router.post("/task-types", response_model=schemas.DimensionOut)(_tt_upsert)
router.delete("/task-types/{item_id}")(_tt_delete)

_ac_list, _ac_upsert, _ac_delete = _dim_router(Activity, "Activity")
router.get("/activities", response_model=list[schemas.DimensionOut])(_ac_list)
router.post("/activities", response_model=schemas.DimensionOut)(_ac_upsert)
router.delete("/activities/{item_id}")(_ac_delete)


# ---------------- budgets ----------------

@router.get("/budgets", response_model=list[schemas.BudgetOut])
def list_budgets(org: Org = Depends(require_org), db: Session = Depends(get_db)):
    rows = db.query(Budget).filter(Budget.org_id == org.id).all()
    return [schemas.BudgetOut(id=b.id, scope_type=b.scope_type, scope_id=b.scope_id,
                              period=b.period, amount_usd=b.amount_usd) for b in rows]


@router.post("/budgets", response_model=schemas.BudgetOut)
def upsert_budget(body: schemas.BudgetIn, p: Principal = Depends(require_admin),
                  db: Session = Depends(get_db)):
    b = (db.query(Budget).filter(Budget.org_id == p.org.id,
                                 Budget.scope_type == body.scope_type,
                                 Budget.scope_id == body.scope_id,
                                 Budget.period == body.period).first())
    if not b:
        b = Budget(org_id=p.org.id, scope_type=body.scope_type,
                   scope_id=body.scope_id, period=body.period)
        db.add(b)
    b.amount_usd = body.amount_usd
    db.commit()
    return schemas.BudgetOut(id=b.id, scope_type=b.scope_type, scope_id=b.scope_id,
                             period=b.period, amount_usd=b.amount_usd)


@router.delete("/budgets/{budget_id}")
def delete_budget(budget_id: str, p: Principal = Depends(require_admin),
                  db: Session = Depends(get_db)):
    b = db.query(Budget).filter(Budget.id == budget_id, Budget.org_id == p.org.id).first()
    if not b:
        raise HTTPException(404, "Budget not found")
    db.delete(b)
    db.commit()
    return {"deleted": budget_id}


# ---------------- sessions ----------------

@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, p: Principal = Depends(require_admin),
                   db: Session = Depends(get_db)):
    """Remove one session and everything hanging off it.

    A demo instance accumulates test rows, and a ledger with obvious junk in it
    is harder to read than one without. Admin only, and scoped to the caller's
    own org.
    """
    from app.models import SessionRow
    row = (db.query(SessionRow)
             .filter(SessionRow.org_id == p.org.id,
                     SessionRow.session_id == session_id).first())
    if not row:
        raise HTTPException(404, "Session not found")
    db.delete(row)          # usage, outcomes and classification cascade
    db.commit()
    return {"deleted": session_id}


# ---------------- MCP tokens ----------------

@router.get("/mcp-tokens")
def list_mcp_tokens(org: Org = Depends(require_org), db: Session = Depends(get_db)):
    rows = db.query(McpToken).filter(McpToken.org_id == org.id).all()
    return [{"id": t.id, "user_email": t.user_email, "label": t.label,
             "created_at": t.created_at, "last_used_at": t.last_used_at,
             "revoked": t.revoked} for t in rows]


@router.post("/mcp-tokens")
def mint_mcp_token(user_email: str, label: str | None = None,
                   p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    """Mint a per-user MCP bearer token. Returned once, in the clear, and only the
    hash is stored. The MCP server derives identity from this token, which is why a
    tool payload can never name the user it is publishing for."""
    email = user_email.lower().strip()
    if not db.query(User).filter(User.org_id == p.org.id, User.email == email).first():
        raise HTTPException(400, f"Unknown user: {email}")
    raw = new_mcp_token()
    tok = McpToken(org_id=p.org.id, user_email=email, token_hash=hash_mcp_token(raw),
                   label=label)
    db.add(tok)
    db.commit()
    return {"id": tok.id, "user_email": email, "label": label, "token": raw,
            "note": "Save this now — only its hash is stored."}


@router.delete("/mcp-tokens/{token_id}")
def revoke_mcp_token(token_id: str, p: Principal = Depends(require_admin),
                     db: Session = Depends(get_db)):
    t = db.query(McpToken).filter(McpToken.id == token_id,
                                  McpToken.org_id == p.org.id).first()
    if not t:
        raise HTTPException(404, "Token not found")
    t.revoked = True
    db.commit()
    return {"revoked": token_id}


# ---------------- productivity baselines ----------------

VALID_SOURCE_TYPES = ("customer_measured", "team_survey", "industry_estimate",
                      "vendor_claim", "unset")


def _bl_out(b: ProductivityBaseline) -> schemas.BaselineOut:
    return schemas.BaselineOut(
        id=b.id, task_type_key=b.task_type_key, unit=b.unit, unit_plural=b.unit_plural,
        human_minutes_per_unit=b.human_minutes_per_unit, minutes_low=b.minutes_low,
        minutes_high=b.minutes_high, source=b.source or "", source_type=b.source_type,
        notes=b.notes, active=b.active, set_by=b.set_by)


@router.get("/baselines", response_model=list[schemas.BaselineOut])
def list_baselines(org: Org = Depends(require_org), db: Session = Depends(get_db)):
    rows = (db.query(ProductivityBaseline)
              .filter(ProductivityBaseline.org_id == org.id)
              .order_by(ProductivityBaseline.task_type_key).all())
    return [_bl_out(b) for b in rows]


@router.post("/baselines", response_model=schemas.BaselineOut)
def upsert_baseline(body: schemas.BaselineIn, p: Principal = Depends(require_admin),
                    db: Session = Depends(get_db)):
    if body.source_type not in VALID_SOURCE_TYPES:
        raise HTTPException(400, f"source_type must be one of {VALID_SOURCE_TYPES}")
    if body.human_minutes_per_unit <= 0:
        raise HTTPException(400, "human_minutes_per_unit must be > 0")
    lo, hi = body.minutes_low, body.minutes_high
    if lo is not None and hi is not None and lo > hi:
        raise HTTPException(400, "minutes_low cannot exceed minutes_high")
    if not db.query(TaskType).filter(TaskType.org_id == p.org.id,
                                     TaskType.key == body.task_type_key).first():
        raise HTTPException(400, f"Unknown task type: {body.task_type_key}")

    b = (db.query(ProductivityBaseline)
           .filter(ProductivityBaseline.org_id == p.org.id,
                   ProductivityBaseline.task_type_key == body.task_type_key,
                   ProductivityBaseline.unit == body.unit).first())
    fields = ("unit_plural", "human_minutes_per_unit", "minutes_low", "minutes_high",
              "source", "source_type", "notes", "active")
    created = b is None
    if created:
        b = ProductivityBaseline(org_id=p.org.id, task_type_key=body.task_type_key,
                                 unit=body.unit)
        db.add(b)
    before = None if created else _snapshot(b, fields)
    for f in fields:
        setattr(b, f, getattr(body, f))
    b.set_by = p.email
    db.flush()
    if created or before != _snapshot(b, fields):
        bump_policy_version(db, p.org.id, p.email)
    db.commit()
    return _bl_out(b)


@router.delete("/baselines/{baseline_id}")
def delete_baseline(baseline_id: str, p: Principal = Depends(require_admin),
                    db: Session = Depends(get_db)):
    b = (db.query(ProductivityBaseline)
           .filter(ProductivityBaseline.id == baseline_id,
                   ProductivityBaseline.org_id == p.org.id).first())
    if not b:
        raise HTTPException(404, "Baseline not found")
    db.delete(b)
    bump_policy_version(db, p.org.id, p.email)
    db.commit()
    return {"deleted": baseline_id}


@router.put("/org/settings")
def update_org_settings(session_minutes_cap: int, p: Principal = Depends(require_admin),
                        db: Session = Depends(get_db)):
    if not (5 <= session_minutes_cap <= 480):
        raise HTTPException(400, "session_minutes_cap must be between 5 and 480")
    p.org.session_minutes_cap = session_minutes_cap
    db.commit()
    return {"session_minutes_cap": p.org.session_minutes_cap}


# ---------------- price book (read-only in the UI) ----------------

@router.get("/price-book")
def list_price_book(_: Org = Depends(require_org), db: Session = Depends(get_db)):
    rows = db.query(PriceBook).order_by(PriceBook.model).all()
    return [{"model": r.model, "input_per_mtok": r.input_per_mtok,
             "output_per_mtok": r.output_per_mtok,
             "cache_read_per_mtok": r.cache_read_per_mtok,
             "cache_write_5m_per_mtok": r.cache_write_5m_per_mtok,
             "cache_write_1h_per_mtok": r.cache_write_1h_per_mtok,
             "version": r.version} for r in rows]
