"""SQLAlchemy models — importing this package registers every table."""

from app.models.audit import AuditLog
from app.models.base import Base, TimestampMixin, UUIDPkMixin, VersionMixin
from app.models.catalog import (
    CatalogCandidateStatus,
    CatalogChangeCandidate,
    CatalogRevision,
    CatalogRevisionStatus,
)
from app.models.effect import EffectLedgerEntry, EffectStatus
from app.models.feedback import (
    FeedbackCluster,
    FeedbackItem,
    FeedbackStatus,
    FeedbackType,
)
from app.models.identity import Role, RoleAssignment, User
from app.models.listing import ExternalIdMapping, ListingPublication, ListingStatus
from app.models.messaging import (
    IdempotencyRecord,
    InboxEvent,
    InboxStatus,
    OutboxEvent,
    OutboxStatus,
)
from app.models.order import SalesOrder, SalesOrderStatus
from app.models.price import PriceOffer, PriceOfferStatus
from app.models.procurement import ProcurementOrder, ProcurementStatus
from app.models.projections import Projection
from app.models.reconciliation import (
    ReconciliationDiff,
    ReconciliationDiffStatus,
    ReconciliationRun,
    ReconciliationRunStatus,
)
from app.models.returns import ReturnCase, ReturnDisposition, ReturnStatus
from app.models.workflow import (
    WorkflowRun,
    WorkflowRunStatus,
    WorkItem,
    WorkItemDecision,
    WorkItemDecisionType,
    WorkItemKind,
    WorkItemStatus,
)

__all__ = [
    "AuditLog",
    "Base",
    "CatalogCandidateStatus",
    "CatalogChangeCandidate",
    "CatalogRevision",
    "CatalogRevisionStatus",
    "EffectLedgerEntry",
    "EffectStatus",
    "ExternalIdMapping",
    "FeedbackCluster",
    "FeedbackItem",
    "FeedbackStatus",
    "FeedbackType",
    "IdempotencyRecord",
    "InboxEvent",
    "InboxStatus",
    "ListingPublication",
    "ListingStatus",
    "OutboxEvent",
    "OutboxStatus",
    "PriceOffer",
    "PriceOfferStatus",
    "ProcurementOrder",
    "ProcurementStatus",
    "Projection",
    "ReconciliationDiff",
    "ReconciliationDiffStatus",
    "ReconciliationRun",
    "ReconciliationRunStatus",
    "ReturnCase",
    "ReturnDisposition",
    "ReturnStatus",
    "Role",
    "RoleAssignment",
    "SalesOrder",
    "SalesOrderStatus",
    "TimestampMixin",
    "UUIDPkMixin",
    "User",
    "VersionMixin",
    "WorkItem",
    "WorkItemDecision",
    "WorkItemDecisionType",
    "WorkItemKind",
    "WorkItemStatus",
    "WorkflowRun",
    "WorkflowRunStatus",
]
