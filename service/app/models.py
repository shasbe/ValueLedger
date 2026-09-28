"""SQLAlchemy models — the ledger.

Grain: one row in `sessions` per Claude session, joined to many `usage_events`
(request grain), one `classification`, and many `outcome_events`.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, relationship


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------------
# Org and identity
# --------------------------------------------------------------------------

class Org(Base):
    __tablename__ = "orgs"
    id = Column(String, primary_key=True, default=_uuid)
    name = Column(String, nullable=False)
    api_key_hash = Column(String, nullable=False, index=True)
    # POC ships `local` classification only (SPEC 4.5). Column exists so the
    # field is in the ledger from day one.
    privacy_mode = Column(String, nullable=False, default="local")
    # Session wall-clock overstates human attention (people walk away), so
    # each session's contribution to "actual human time" is capped.
    session_minutes_cap = Column(Integer, nullable=False, default=90)
    created_at = Column(DateTime, default=_now)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("org_id", "email", name="uq_user_org_email"),)
    id = Column(String, primary_key=True, default=_uuid)
    org_id = Column(String, ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True)
    email = Column(String, nullable=False, index=True)
    display_name = Column(String)
    cost_center = Column(String, index=True)
    manager_email = Column(String)
    # member | initiative_owner | finance | admin
    role = Column(String, nullable=False, default="member")
    created_at = Column(DateTime, default=_now)


# --------------------------------------------------------------------------
# Attribution Policy — a versioned prompt bundle (SPEC 5.1)
# --------------------------------------------------------------------------

class AttributionPolicy(Base):
    __tablename__ = "attribution_policies"
    __table_args__ = (UniqueConstraint("org_id", "version", name="uq_policy_org_version"),)
    id = Column(String, primary_key=True, default=_uuid)
    org_id = Column(String, ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    published_at = Column(DateTime, default=_now)
    published_by = Column(String)
    checksum = Column(String)
    global_guidance = Column(Text, default="")
    output_extraction_guidance = Column(Text, default="")


class Initiative(Base):
    __tablename__ = "initiatives"
    __table_args__ = (UniqueConstraint("org_id", "key", name="uq_initiative_org_key"),)
    id = Column(String, primary_key=True, default=_uuid)
    org_id = Column(String, ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True)
    policy_version = Column(Integer, nullable=False, default=1)
    key = Column(String, nullable=False)
    name = Column(String, nullable=False)
    description = Column(Text, default="")
    classification_guidance = Column(Text, default="")
    owner_email = Column(String, index=True)
    budget_amount = Column(Float)
    budget_period = Column(String)          # e.g. 2026-Q3
    cost_center = Column(String, index=True)
    status = Column(String, default="active")
    created_at = Column(DateTime, default=_now)


class TaskType(Base):
    __tablename__ = "task_types"
    __table_args__ = (UniqueConstraint("org_id", "key", name="uq_tasktype_org_key"),)
    id = Column(String, primary_key=True, default=_uuid)
    org_id = Column(String, ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True)
    policy_version = Column(Integer, nullable=False, default=1)
    key = Column(String, nullable=False)
    name = Column(String, nullable=False)
    description = Column(Text, default="")
    classification_guidance = Column(Text, default="")


class Activity(Base):
    __tablename__ = "activities"
    __table_args__ = (UniqueConstraint("org_id", "key", name="uq_activity_org_key"),)
    id = Column(String, primary_key=True, default=_uuid)
    org_id = Column(String, ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True)
    policy_version = Column(Integer, nullable=False, default=1)
    key = Column(String, nullable=False)
    name = Column(String, nullable=False)
    description = Column(Text, default="")
    classification_guidance = Column(Text, default="")


class PolicyExample(Base):
    """Few-shot examples, promoted from user corrections (SPEC 5.1)."""
    __tablename__ = "policy_examples"
    id = Column(String, primary_key=True, default=_uuid)
    org_id = Column(String, ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True)
    policy_version = Column(Integer, nullable=False, default=1)
    dimension = Column(String, nullable=False)    # initiative | task_type | activity
    example_text = Column(Text, nullable=False)
    label = Column(String, nullable=False)
    source_correction_id = Column(String)
    created_at = Column(DateTime, default=_now)


# --------------------------------------------------------------------------
# The ledger
# --------------------------------------------------------------------------

class SessionRow(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        UniqueConstraint("org_id", "session_id", name="uq_session_org_sid"),
        Index("ix_sessions_org_started", "org_id", "started_at"),
    )
    id = Column(String, primary_key=True, default=_uuid)
    org_id = Column(String, ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True)
    session_id = Column(String, nullable=False, index=True)
    surface = Column(String, nullable=False)          # claude_code | cowork | chat
    user_email = Column(String, nullable=False, index=True)
    principal_type = Column(String, default="human")  # human | agent | scheduled
    parent_session_id = Column(String, index=True)    # from isSidechain
    repo = Column(String, index=True)
    branch = Column(String)
    cwd_hash = Column(String)
    started_at = Column(DateTime, index=True)
    ended_at = Column(DateTime)
    client_version = Column(String)
    collection_method = Column(String, nullable=False, default="collector")  # collector | mcp_skill
    cost_basis = Column(String, nullable=False, default="measured")          # measured | unknown
    external_ref = Column(String, index=True)
    created_at = Column(DateTime, default=_now)

    usage_events = relationship("UsageEvent", back_populates="session", cascade="all, delete-orphan")
    outcomes = relationship("OutcomeEvent", back_populates="session", cascade="all, delete-orphan")
    classification = relationship(
        "Classification", back_populates="session", uselist=False, cascade="all, delete-orphan"
    )


class UsageEvent(Base):
    """Request grain. Raw token counts are kept so cost is recomputable (SPEC 6)."""
    __tablename__ = "usage_events"
    __table_args__ = (
        UniqueConstraint("session_pk", "record_uuid", name="uq_usage_session_record"),
    )
    id = Column(String, primary_key=True, default=_uuid)
    session_pk = Column(String, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    record_uuid = Column(String, nullable=False)     # transcript record uuid — dedupe key
    ts = Column(DateTime, index=True)
    model = Column(String, nullable=False, index=True)
    request_id = Column(String)
    input_tokens = Column(Integer, default=0)
    output_tokens = Column(Integer, default=0)
    cache_read_tokens = Column(Integer, default=0)
    cache_write_5m_tokens = Column(Integer, default=0)
    cache_write_1h_tokens = Column(Integer, default=0)
    web_search_requests = Column(Integer, default=0)
    cost_usd = Column(Float, default=0.0)
    price_book_version = Column(String)
    priced = Column(Boolean, default=True)           # False when model is unknown to price book

    session = relationship("SessionRow", back_populates="usage_events")


class Classification(Base):
    __tablename__ = "classifications"
    id = Column(String, primary_key=True, default=_uuid)
    session_pk = Column(String, ForeignKey("sessions.id", ondelete="CASCADE"),
                        nullable=False, unique=True, index=True)
    initiative_id = Column(String, ForeignKey("initiatives.id"), index=True)
    task_type_id = Column(String, ForeignKey("task_types.id"), index=True)
    activity_id = Column(String, ForeignKey("activities.id"), index=True)
    conf_initiative = Column(Float)
    conf_task_type = Column(Float)
    conf_activity = Column(Float)
    rationale = Column(Text)
    classifier_model = Column(String)
    classifier_cost_usd = Column(Float, default=0.0)   # self-metering (SPEC 5.4)
    classifier_version = Column(String)
    policy_version = Column(Integer)
    # auto | needs_review | confirmed | corrected | unclassifiable
    status = Column(String, nullable=False, default="auto", index=True)
    created_at = Column(DateTime, default=_now)

    session = relationship("SessionRow", back_populates="classification")


class ClassificationCorrection(Base):
    __tablename__ = "classification_corrections"
    id = Column(String, primary_key=True, default=_uuid)
    classification_id = Column(String, ForeignKey("classifications.id", ondelete="CASCADE"),
                               nullable=False, index=True)
    corrected_by = Column(String, nullable=False)
    prev_json = Column(Text)
    new_json = Column(Text)
    note = Column(Text)
    ts = Column(DateTime, default=_now)


class OutcomeEvent(Base):
    __tablename__ = "outcome_events"
    __table_args__ = (
        UniqueConstraint("session_pk", "type", "ref", name="uq_outcome_session_type_ref"),
    )
    id = Column(String, primary_key=True, default=_uuid)
    org_id = Column(String, ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True)
    session_pk = Column(String, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    # commit | pr_opened | pr_merged | pr_closed | test_added | revert | file_changed
    type = Column(String, nullable=False, index=True)
    ref = Column(String, nullable=False)
    ts = Column(DateTime)
    metadata_json = Column(Text)

    session = relationship("SessionRow", back_populates="outcomes")


class McpToken(Base):
    """Per-user bearer token for the MCP server.

    This is how the trust boundary is actually enforced rather than asserted: the
    MCP server derives org and user identity from the token, so a model-generated
    payload can never name the user it is publishing for.
    """
    __tablename__ = "mcp_tokens"
    id = Column(String, primary_key=True, default=_uuid)
    org_id = Column(String, ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True)
    user_email = Column(String, nullable=False, index=True)
    token_hash = Column(String, nullable=False, unique=True, index=True)
    label = Column(String)
    created_at = Column(DateTime, default=_now)
    last_used_at = Column(DateTime)
    revoked = Column(Boolean, default=False)


class OAuthClient(Base):
    """A client registered via RFC 7591 Dynamic Client Registration.

    claude.ai registers itself this way — there is no console to paste a client
    id into, so the server has to mint one on demand.
    """
    __tablename__ = "oauth_clients"
    id = Column(String, primary_key=True, default=_uuid)
    client_id = Column(String, nullable=False, unique=True, index=True)
    client_name = Column(String)
    redirect_uris = Column(Text, nullable=False)     # JSON array
    created_at = Column(DateTime, default=_now)


class OAuthCode(Base):
    """A short-lived authorization code, bound to a PKCE challenge.

    Carries the identity the human chose on the consent screen, so the token
    minted from it is bound to that user — identity is decided here, once, by a
    human, and never by anything the model sends later.
    """
    __tablename__ = "oauth_codes"
    id = Column(String, primary_key=True, default=_uuid)
    code = Column(String, nullable=False, unique=True, index=True)
    client_id = Column(String, nullable=False, index=True)
    redirect_uri = Column(String, nullable=False)
    code_challenge = Column(String)
    code_challenge_method = Column(String, default="S256")
    org_id = Column(String, ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False)
    user_email = Column(String, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used = Column(Boolean, default=False)


class ProductivityBaseline(Base):
    """What a unit of work costs a human, per the customer's own estimate.

    This is the one assumption in the product, so it is stored as a first-class,
    versioned, attributed record rather than a constant in code. Every
    productivity figure is rendered with the baseline and its source attached,
    and a low/high band so the reader can see how much the conclusion depends on
    the assumption.
    """
    __tablename__ = "productivity_baselines"
    __table_args__ = (
        UniqueConstraint("org_id", "task_type_key", "unit", name="uq_baseline_org_tt_unit"),
    )
    id = Column(String, primary_key=True, default=_uuid)
    org_id = Column(String, ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True)
    task_type_key = Column(String, nullable=False, index=True)
    unit = Column(String, nullable=False)              # slide | merged_pr | document | analysis
    unit_plural = Column(String)
    human_minutes_per_unit = Column(Float, nullable=False)
    minutes_low = Column(Float)                        # sensitivity band
    minutes_high = Column(Float)
    source = Column(Text)                              # "2026 design team time study"
    # customer_measured | team_survey | industry_estimate | vendor_claim | unset
    source_type = Column(String, nullable=False, default="unset")
    set_by = Column(String)
    set_at = Column(DateTime, default=_now)
    notes = Column(Text)
    active = Column(Boolean, default=True)


class WorkOutput(Base):
    """Units of work a session produced — the measured numerator.

    `basis` records how we know: `measured` from git or an outcome event,
    `model_extracted` when the classifier counted them from the transcript.
    Never collapsed, because they are not equally trustworthy.
    """
    __tablename__ = "work_outputs"
    __table_args__ = (
        UniqueConstraint("session_pk", "unit", name="uq_workoutput_session_unit"),
    )
    id = Column(String, primary_key=True, default=_uuid)
    session_pk = Column(String, ForeignKey("sessions.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    unit = Column(String, nullable=False, index=True)
    count = Column(Float, nullable=False, default=0.0)
    basis = Column(String, nullable=False, default="model_extracted")  # measured | model_extracted
    detail = Column(Text)


class PriceBook(Base):
    __tablename__ = "price_book"
    __table_args__ = (UniqueConstraint("model", "effective_from", name="uq_price_model_from"),)
    id = Column(String, primary_key=True, default=_uuid)
    model = Column(String, nullable=False, index=True)
    effective_from = Column(DateTime, nullable=False, default=_now)
    input_per_mtok = Column(Float, nullable=False)
    output_per_mtok = Column(Float, nullable=False)
    cache_read_per_mtok = Column(Float, nullable=False)
    cache_write_5m_per_mtok = Column(Float, nullable=False)
    cache_write_1h_per_mtok = Column(Float, nullable=False)
    version = Column(String, default="v1")


class Budget(Base):
    __tablename__ = "budgets"
    id = Column(String, primary_key=True, default=_uuid)
    org_id = Column(String, ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True)
    scope_type = Column(String, nullable=False)   # org | initiative | cost_center
    scope_id = Column(String)
    period = Column(String, nullable=False)       # 2026-Q3
    amount_usd = Column(Float, nullable=False)
