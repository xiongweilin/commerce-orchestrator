"""Append-only realization and reconciliation semantic records."""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import select

from app.core.errors import NotFoundError, ValidationError
from app.models.effect import EffectLedgerEntry
from app.models.effect_realization import (
    EffectRealizationAssessment,
    EffectRealizationStatus,
)
from app.models.identity import User
from app.models.reconciliation import ReconciliationDiff
from app.models.reconciliation_resolution import (
    ReconciliationResolution,
    ReconciliationResolutionKind,
    ReconciliationVerificationStatus,
)


def _refs(values: Iterable[str]) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()
    for value in values:
        ref = str(value).strip()
        if ref and ref not in seen:
            seen.add(ref)
            refs.append(ref)
    return refs


def _require_actor(db, actor_user_id: uuid.UUID) -> uuid.UUID:
    if db.get(User, actor_user_id) is None:
        raise NotFoundError("semantic judgment actor not found")
    return actor_user_id


def append_effect_realization_assessment(
    db,
    *,
    effect_id: uuid.UUID,
    realization_status: EffectRealizationStatus | str,
    assessed_by_user_id: uuid.UUID,
    evidence_refs: Iterable[str] = (),
) -> EffectRealizationAssessment:
    """Append reality judgment without rewriting execution history."""
    effect = db.get(EffectLedgerEntry, effect_id)
    if effect is None:
        raise NotFoundError("effect ledger entry not found")
    status = EffectRealizationStatus(realization_status)
    evidence = _refs(evidence_refs)
    if status is not EffectRealizationStatus.UNVERIFIED and not evidence:
        raise ValidationError(
            f"{status.value} effect realization assessment requires evidence_refs"
        )
    assessment = EffectRealizationAssessment(
        effect_id=effect.id,
        realization_status=status,
        evidence_refs=evidence,
        assessed_by_user_id=_require_actor(db, assessed_by_user_id),
    )
    db.add(assessment)
    db.flush()
    return assessment


def latest_effect_realization_assessment(
    db, effect_id: uuid.UUID
) -> EffectRealizationAssessment | None:
    return (
        db.execute(
            select(EffectRealizationAssessment)
            .where(EffectRealizationAssessment.effect_id == effect_id)
            .order_by(
                EffectRealizationAssessment.assessed_at.desc(),
                EffectRealizationAssessment.id.desc(),
            )
            .limit(1)
        )
        .scalars()
        .first()
    )


def append_reconciliation_resolution(
    db,
    *,
    reconciliation_diff_id: uuid.UUID,
    resolution_kind: ReconciliationResolutionKind | str,
    verification_status: ReconciliationVerificationStatus | str,
    recorded_by_user_id: uuid.UUID,
    basis_refs: Iterable[str],
    evidence_refs: Iterable[str] = (),
) -> ReconciliationResolution:
    """Append disposition/verification without rewriting diff lifecycle."""
    diff = db.get(ReconciliationDiff, reconciliation_diff_id)
    if diff is None:
        raise NotFoundError("reconciliation diff not found")
    kind = ReconciliationResolutionKind(resolution_kind)
    verification = ReconciliationVerificationStatus(verification_status)
    bases = _refs(basis_refs)
    evidence = _refs(evidence_refs)
    if not bases:
        raise ValidationError("reconciliation resolution requires basis_refs")
    if verification is not ReconciliationVerificationStatus.UNVERIFIED and not evidence:
        raise ValidationError(
            f"{verification.value} reconciliation verification requires evidence_refs"
        )
    resolution = ReconciliationResolution(
        reconciliation_diff_id=diff.id,
        resolution_kind=kind,
        verification_status=verification,
        basis_refs=bases,
        evidence_refs=evidence,
        recorded_by_user_id=_require_actor(db, recorded_by_user_id),
    )
    db.add(resolution)
    db.flush()
    return resolution


def latest_reconciliation_resolution(
    db, reconciliation_diff_id: uuid.UUID
) -> ReconciliationResolution | None:
    return (
        db.execute(
            select(ReconciliationResolution)
            .where(ReconciliationResolution.reconciliation_diff_id == reconciliation_diff_id)
            .order_by(
                ReconciliationResolution.recorded_at.desc(),
                ReconciliationResolution.id.desc(),
            )
            .limit(1)
        )
        .scalars()
        .first()
    )


__all__ = [
    "append_effect_realization_assessment",
    "append_reconciliation_resolution",
    "latest_effect_realization_assessment",
    "latest_reconciliation_resolution",
]
