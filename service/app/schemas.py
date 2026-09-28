"""Pydantic request/response models."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


# ---------------- admin / setup ----------------

class OrgCreate(BaseModel):
    name: str


class OrgOut(BaseModel):
    id: str
    name: str
    privacy_mode: str
    session_minutes_cap: int = 90
    api_key: str | None = None   # returned only at creation


class UserIn(BaseModel):
    email: str
    display_name: str | None = None
    cost_center: str | None = None
    manager_email: str | None = None
    role: str = "member"


class UserOut(UserIn):
    id: str


class InitiativeIn(BaseModel):
    key: str
    name: str
    description: str = ""
    classification_guidance: str = ""
    owner_email: str | None = None
    budget_amount: float | None = None
    budget_period: str | None = None
    cost_center: str | None = None
    status: str = "active"


class InitiativeOut(InitiativeIn):
    id: str
    policy_version: int


class DimensionIn(BaseModel):
    key: str
    name: str
    description: str = ""
    classification_guidance: str = ""


class DimensionOut(DimensionIn):
    id: str
    policy_version: int


class BaselineIn(BaseModel):
    task_type_key: str
    unit: str
    unit_plural: str | None = None
    human_minutes_per_unit: float
    minutes_low: float | None = None
    minutes_high: float | None = None
    source: str = ""
    # customer_measured | team_survey | industry_estimate | vendor_claim | unset
    source_type: str = "unset"
    notes: str | None = None
    active: bool = True


class BaselineOut(BaselineIn):
    id: str
    set_by: str | None = None


class BudgetIn(BaseModel):
    scope_type: str          # org | initiative | cost_center
    scope_id: str | None = None
    period: str
    amount_usd: float


class BudgetOut(BudgetIn):
    id: str


# ---------------- policy bundle (SPEC 5.1) ----------------

class PolicyDimension(BaseModel):
    key: str
    name: str
    description: str = ""
    classification_guidance: str = ""


class PolicyExampleOut(BaseModel):
    dimension: str
    example_text: str
    label: str


class PolicyBaseline(BaseModel):
    """Tells the classifier which units to count for a task type, and what each
    is assumed to cost a human."""
    task_type: str
    unit: str
    unit_plural: str | None = None
    human_minutes_per_unit: float
    range_minutes: list[float] | None = None
    source: str = ""
    source_type: str = "unset"


class PolicyBundle(BaseModel):
    policy_version: int
    global_guidance: str = ""
    initiatives: list[PolicyDimension]
    task_types: list[PolicyDimension]
    activities: list[PolicyDimension]
    examples: list[PolicyExampleOut] = Field(default_factory=list)
    output_extraction_guidance: str = ""
    baselines: list[PolicyBaseline] = Field(default_factory=list)


class PolicyUpdate(BaseModel):
    global_guidance: str | None = None
    output_extraction_guidance: str | None = None


# ---------------- ingest ----------------

class UsageEventIn(BaseModel):
    record_uuid: str
    ts: datetime | None = None
    model: str
    request_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_5m_tokens: int = 0
    cache_write_1h_tokens: int = 0
    web_search_requests: int = 0


class OutcomeIn(BaseModel):
    type: str
    ref: str
    ts: datetime | None = None
    metadata: dict | None = None


class WorkUnitIn(BaseModel):
    """Units of work a session produced. `basis` says how we know — `measured`
    from git/outcomes, `model_extracted` when the classifier counted them."""
    unit: str
    count: float
    basis: str = "model_extracted"
    detail: str | None = None


class ClassificationIn(BaseModel):
    initiative_key: str | None = None
    task_type_key: str | None = None
    activity_key: str | None = None
    conf_initiative: float | None = None
    conf_task_type: float | None = None
    conf_activity: float | None = None
    rationale: str | None = None
    classifier_model: str | None = None
    classifier_cost_usd: float = 0.0
    classifier_version: str | None = None
    policy_version: int | None = None
    status: str | None = None
    work_units: list[WorkUnitIn] = Field(default_factory=list)


class SessionIn(BaseModel):
    session_id: str
    surface: str                       # claude_code | cowork | chat
    user_email: str
    principal_type: str = "human"
    parent_session_id: str | None = None
    repo: str | None = None
    branch: str | None = None
    cwd_hash: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    client_version: str | None = None
    collection_method: str = "collector"
    cost_basis: str = "measured"
    external_ref: str | None = None
    usage: list[UsageEventIn] = Field(default_factory=list)
    outcomes: list[OutcomeIn] = Field(default_factory=list)
    classification: ClassificationIn | None = None


class EventsBatch(BaseModel):
    sessions: list[SessionIn]


class IngestResult(BaseModel):
    sessions_received: int
    sessions_created: int
    sessions_updated: int
    usage_events_added: int
    usage_events_deduped: int
    outcomes_added: int
    cost_usd_added: float
    unpriced_models: list[str] = Field(default_factory=list)


# ---------------- corrections ----------------

class CorrectionIn(BaseModel):
    initiative_key: str | None = None
    task_type_key: str | None = None
    activity_key: str | None = None
    note: str | None = None
    promote_example: bool = True
