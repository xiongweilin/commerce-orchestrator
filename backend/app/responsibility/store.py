"""SQLAlchemy-backed narrow store for portable Experience contracts.

The adapter deliberately implements only the semantic records needed by the
Experience public/reference seam. It is not a second workflow store and does
not expose portable governance/dispatch authority to Commerce.
"""

from __future__ import annotations

from typing import Any

from portable_runtime.core.models import Event
from portable_runtime.experience.historical_use import (
    HistoricalExperienceUse,
    HistoricalExperienceUseCommitRequest,
    prepare_historical_experience_use_commit,
)
from portable_runtime.records.authorization import AuthorizationGrant
from portable_runtime.records.knowledge import (
    KnowledgeProjection,
    validate_projection_for_official,
)
from portable_runtime.records.models import (
    ActionRecord,
    Assertion,
    BaseRecord,
    ChangeObjectRecord,
    Constraint,
    DecisionRecord,
    Derivation,
    EvidenceArtifact,
    Experiment,
    Goal,
    Observation,
    OutcomeRecord,
    PolicyRecord,
    RevisionRecord,
)
from portable_runtime.records.relations import RecordRelation, validate_relation
from portable_runtime.records.validation import validate_canonical_write, validate_record
from sqlalchemy import select

from app.core.errors import ConflictError, ValidationError
from app.models.responsibility import (
    ResponsibilityEvent,
    ResponsibilityKnowledgeProjection,
    ResponsibilityRecord,
)

_RECORD_TYPES: dict[str, type[BaseRecord]] = {
    "EvidenceArtifact": EvidenceArtifact,
    "Observation": Observation,
    "Assertion": Assertion,
    "Goal": Goal,
    "Constraint": Constraint,
    "Experiment": Experiment,
    "Decision": DecisionRecord,
    "Action": ActionRecord,
    "Outcome": OutcomeRecord,
    "Revision": RevisionRecord,
    "ChangeObject": ChangeObjectRecord,
    "Policy": PolicyRecord,
    "Derivation": Derivation,
}
_RELATION_EVENT_TYPE = "ResponsibilityRelationRecorded"
_AUTHORIZATION_EVENT_TYPE = "ResponsibilityAuthorizationRecorded"


def _semantic_payload(value: Any) -> dict[str, Any]:
    payload = value.model_dump(mode="json")
    payload.pop("created_at", None)
    return payload


def parse_portable_record(payload: dict[str, Any]) -> BaseRecord:
    base = BaseRecord.model_validate(payload)
    model_type = _RECORD_TYPES.get(base.record_type)
    if model_type is None:
        raise ValidationError(
            f"unsupported portable responsibility record type: {base.record_type}"
        )
    return model_type.model_validate(payload)


class CommerceResponsibilityStore:
    """Session-bound persistence adapter for portable Experience semantics."""

    def __init__(self, db) -> None:
        self.db = db

    def get_record(self, record_id: str) -> BaseRecord | None:
        row = self.db.get(ResponsibilityRecord, record_id)
        if row is None:
            return None
        return parse_portable_record(row.payload)

    def list_records(self, record_type: str | None = None) -> list[BaseRecord]:
        stmt = select(ResponsibilityRecord).order_by(ResponsibilityRecord.created_at)
        if record_type is not None:
            stmt = stmt.where(ResponsibilityRecord.record_type == record_type)
        return [parse_portable_record(row.payload) for row in self.db.execute(stmt).scalars()]

    def save_record(self, value: BaseRecord) -> None:
        existing = self.get_record(value.id)
        if existing is not None:
            if _semantic_payload(existing) != _semantic_payload(value):
                raise ConflictError(
                    f"responsibility record {value.id!r} is append-only; semantic rebound refused"
                )
            return
        errors = [*validate_record(value), *validate_canonical_write(value)]
        if errors:
            raise ValidationError("invalid portable responsibility record: " + "; ".join(errors))
        if value.record_type not in _RECORD_TYPES:
            raise ValidationError(
                f"unsupported portable responsibility record type: {value.record_type}"
            )
        self.db.add(
            ResponsibilityRecord(
                id=value.id,
                record_type=value.record_type,
                payload=value.model_dump(mode="json"),
                created_at=value.created_at,
            )
        )
        self.db.flush()

    def get_knowledge_projection(self, projection_id: str) -> KnowledgeProjection | None:
        row = self.db.get(ResponsibilityKnowledgeProjection, projection_id)
        return None if row is None else KnowledgeProjection.model_validate(row.payload)

    def list_knowledge_projections(self) -> list[KnowledgeProjection]:
        rows = self.db.execute(
            select(ResponsibilityKnowledgeProjection).order_by(
                ResponsibilityKnowledgeProjection.created_at
            )
        ).scalars()
        return [KnowledgeProjection.model_validate(row.payload) for row in rows]

    def save_knowledge_projection(self, value: KnowledgeProjection) -> None:
        existing = self.get_knowledge_projection(value.id)
        if existing is not None:
            if _semantic_payload(existing) != _semantic_payload(value):
                raise ConflictError(
                    f"knowledge projection {value.id!r} is immutable in Commerce; create a new id"
                )
            return
        if value.lifecycle_status == "official":
            errors = validate_projection_for_official(value)
            if errors:
                raise ValidationError(
                    "cannot persist official KnowledgeProjection: " + "; ".join(errors)
                )
        self.db.add(
            ResponsibilityKnowledgeProjection(
                id=value.id,
                payload=value.model_dump(mode="json"),
                created_at=value.created_at,
                updated_at=value.updated_at,
            )
        )
        self.db.flush()

    def get_event(self, event_id: str) -> Event | None:
        row = self.db.get(ResponsibilityEvent, event_id)
        return None if row is None else Event.model_validate(row.payload)

    def list_events(self, subject_ref: str | None = None) -> list[Event]:
        stmt = select(ResponsibilityEvent).order_by(ResponsibilityEvent.created_at)
        if subject_ref is not None:
            stmt = stmt.where(ResponsibilityEvent.subject_ref == subject_ref)
        return [Event.model_validate(row.payload) for row in self.db.execute(stmt).scalars()]

    def append_event(self, value: Event) -> None:
        existing = self.get_event(value.id)
        if existing is not None:
            if _semantic_payload(existing) != _semantic_payload(value):
                raise ConflictError(f"responsibility event {value.id!r} is immutable")
            return
        self.db.add(
            ResponsibilityEvent(
                id=value.id,
                event_type=value.type,
                subject_ref=value.subject_ref,
                payload=value.model_dump(mode="json"),
                created_at=value.created_at,
            )
        )
        self.db.flush()

    def save_relation(self, value: RecordRelation) -> None:
        """Persist a portable relation as an append-only responsibility event."""

        errors = validate_relation(value)
        if errors:
            raise ValidationError("invalid portable responsibility relation: " + "; ".join(errors))
        event = Event(
            id=value.id,
            type=_RELATION_EVENT_TYPE,
            subject_ref=value.subject_ref,
            payload={"relation": value.model_dump(mode="json")},
            created_at=value.created_at,
        )
        self.append_event(event)

    def save_authorization(self, value: AuthorizationGrant) -> None:
        """Persist explicit governance provenance; this does not authorize Commerce effects."""

        event = Event(
            id=value.id,
            type=_AUTHORIZATION_EVENT_TYPE,
            subject_ref=value.id,
            payload={"authorization": value.model_dump(mode="json")},
            created_at=value.created_at,
        )
        self.append_event(event)

    def export_state(self) -> dict[str, list[dict[str, object]]]:
        """Return one coherent view used by the portable Experience evaluator."""

        events = self.list_events()
        relations: list[dict[str, object]] = []
        authorizations: list[dict[str, object]] = []
        for event in events:
            if event.type == _RELATION_EVENT_TYPE:
                raw = event.payload.get("relation")
                if isinstance(raw, dict):
                    relations.append(RecordRelation.model_validate(raw).model_dump(mode="json"))
            elif event.type == _AUTHORIZATION_EVENT_TYPE:
                raw = event.payload.get("authorization")
                if isinstance(raw, dict):
                    authorizations.append(
                        AuthorizationGrant.model_validate(raw).model_dump(mode="json")
                    )

        return {
            "record": [record.model_dump(mode="json") for record in self.list_records()],
            "knowledge_projection": [
                projection.model_dump(mode="json")
                for projection in self.list_knowledge_projections()
            ],
            "event": [event.model_dump(mode="json") for event in events],
            "relation": relations,
            "authorization": authorizations,
            "authorization_use": [],
        }

    def commit_historical_experience_use(
        self,
        request: HistoricalExperienceUseCommitRequest,
    ) -> HistoricalExperienceUse:
        """Store-owned compare-and-bind in the caller's SQL transaction."""

        prepared = prepare_historical_experience_use_commit(self, request)
        if prepared.replayed:
            return prepared.binding
        self.save_record(prepared.judgment)
        self.append_event(prepared.event)
        return prepared.binding


__all__ = ["CommerceResponsibilityStore", "parse_portable_record"]