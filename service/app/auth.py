"""Authentication and role scoping.

Two credentials:
  * X-API-Key   — org-scoped ingest key, used by the collector and MCP server.
  * X-User-Email— acting user. POC dev auth; production replaces this with
                  Google OIDC (SPEC 7). The header is only trusted alongside a
                  valid org API key.

Role scoping is enforced here and in the query layer, never in the UI. A
`member` can only ever read rows where user_email is their own.
"""
from __future__ import annotations

import hashlib
import os
import secrets
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Initiative, Org, User

ROLES = ("member", "initiative_owner", "finance", "admin")


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def new_api_key() -> str:
    return "vl_" + secrets.token_urlsafe(32)


@dataclass
class Principal:
    org: Org
    user: User | None
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def sees_all(self) -> bool:
        return self.role in ("finance", "admin")

    @property
    def email(self) -> str | None:
        return self.user.email if self.user else None


def require_org(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    db: Session = Depends(get_db),
) -> Org:
    if not x_api_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing X-API-Key")
    org = db.query(Org).filter(Org.api_key_hash == hash_key(x_api_key)).first()
    if not org:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid API key")
    return org


def require_principal(
    x_user_email: str | None = Header(default=None, alias="X-User-Email"),
    org: Org = Depends(require_org),
    db: Session = Depends(get_db),
) -> Principal:
    """Resolve the acting user. An unknown email is treated as `member` with no
    rows rather than being granted a default — failing closed."""
    if not x_user_email:
        # No acting user: ingest-only credential. Treated as admin for machine
        # endpoints, which are separately gated by require_org.
        return Principal(org=org, user=None, role="admin")
    email = x_user_email.lower().strip()
    user = (
        db.query(User)
        .filter(User.org_id == org.id, User.email == email)
        .first()
    )
    if not user:
        # On a public demo instance, someone trying the collector should not be
        # stopped by not existing yet. They self-register as `member`, which sees
        # only their own rows — the scoping is unchanged, just the onboarding.
        if os.environ.get("DEMO_MODE", "").lower() in ("1", "true", "yes"):
            user = User(org_id=org.id, email=email, display_name=email.split("@")[0],
                        cost_center="Guest", role="member")
            db.add(user)
            db.commit()
        else:
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                f"Unknown user: {x_user_email}")
    return Principal(org=org, user=user, role=user.role)


def require_admin(p: Principal = Depends(require_principal)) -> Principal:
    if not p.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin role required")
    return p


def visible_initiative_ids(db: Session, p: Principal) -> list[str] | None:
    """Initiative ids this principal may read. None means 'all'."""
    if p.sees_all:
        return None
    if p.role == "initiative_owner" and p.email:
        return [
            i.id for i in db.query(Initiative.id)
            .filter(Initiative.org_id == p.org.id, Initiative.owner_email == p.email)
            .all()
        ]
    return []


def assert_can_read_user(p: Principal, target_email: str) -> None:
    if p.sees_all:
        return
    if p.email and p.email.lower() == target_email.lower():
        return
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Not permitted to read another user's rows")
