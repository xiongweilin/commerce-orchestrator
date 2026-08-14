from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class ProblemType(StrEnum):
    BUG = "bug"
    HOW_TO = "how_to"
    CONFIGURATION = "configuration"
    PERFORMANCE = "performance"
    INTEGRATION = "integration"
    PERMISSION = "permission"
    DATA_CONSISTENCY = "data_consistency"
    FEATURE_REQUEST = "feature_request"


class ProductArea(StrEnum):
    PROJECT = "project"
    TASK = "task"
    MEMBER_PERMISSION = "member_permission"
    NOTIFICATION = "notification"
    FILE = "file"
    IMPORT_EXPORT = "import_export"
    OPEN_API = "open_api"
    ACCOUNT_SUBSCRIPTION = "account_subscription"


class Sentiment(StrEnum):
    CALM = "calm"
    CONFUSED = "confused"
    FRUSTRATED = "frustrated"
    ANGRY = "angry"


class Owner(StrEnum):
    CUSTOMER_SUCCESS = "customer_success"
    IMPLEMENTATION_SUPPORT = "implementation_support"
    TECHNICAL_SUPPORT = "technical_support"
    QA_TRIAGE = "qa_triage"
    ENGINEERING_TRIAGE = "engineering_triage"
    PRODUCT_OPS = "product_ops"


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ReviewStatus(StrEnum):
    ACCEPTED = "accepted"
    CORRECTED = "corrected"
    REJECTED = "rejected"
    NEEDS_REVIEW = "needs_review"


class AffectedScope(StrEnum):
    INDIVIDUAL = "individual"
    TEAM = "team"
    ORGANIZATION = "organization"


class TicketInput(BaseModel):
    ticket_id: str = Field(min_length=1, max_length=64)
    user_type: str = Field(default="member", max_length=32)
    channel: str = Field(default="support", max_length=32)
    message: str = Field(min_length=3, max_length=2_000)
    created_at: datetime
    current_status: str = Field(default="open", max_length=32)

    @field_validator("message")
    @classmethod
    def strip_message(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message must not be blank")
        return value


class EvidenceQuote(BaseModel):
    quote: str = Field(min_length=2, max_length=500)


class ImpactSignals(BaseModel):
    affected_scope: AffectedScope = AffectedScope.INDIVIDUAL
    workflow_blocked: bool = False
    data_loss_claimed: bool = False
    repeat_contacts: int = Field(default=0, ge=0, le=20)


class LLMAnalysis(BaseModel):
    summary: str = Field(min_length=3, max_length=200)
    issue_signature: str | None = Field(default=None, min_length=3, max_length=80)
    problem_type: ProblemType
    product_area: ProductArea
    sentiment: Sentiment
    llm_owner_suggestion: Owner | None = None
    root_cause_hypothesis: str | None = Field(default=None, max_length=300)
    impact_signals: ImpactSignals
    evidence_spans: list[EvidenceQuote] = Field(min_length=1, max_length=5)


class LocatedEvidence(BaseModel):
    quote: str
    start: int
    end: int
    match_method: str
    match_count: int


class FinalAnalysis(BaseModel):
    summary: str
    issue_signature: str
    problem_type: ProblemType
    product_area: ProductArea
    sentiment: Sentiment
    suggested_owner: Owner
    llm_owner_suggestion: Owner | None = None
    root_cause_hypothesis: str | None = None
    impact_signals: ImpactSignals
    evidence_spans: list[LocatedEvidence]
    severity: Severity
    needs_escalation: bool
    review_status: ReviewStatus
    review_reasons: list[str]
    workflow_version: str
    analysis_source: str
    classification_source: str = "llm"
    routing_policy_version: str = "feedback-routing-v1"


class ReviewPatch(BaseModel):
    status: ReviewStatus
    corrected_problem_type: ProblemType | None = None
    corrected_product_area: ProductArea | None = None
    corrected_owner: Owner | None = None
    note: str | None = Field(default=None, max_length=500)
