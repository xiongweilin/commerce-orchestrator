"""Commerce current-use composition over portable Experience admission.

Portable ExperienceUseAdmission remains the semantic oracle for current
Experience qualification. Commerce ResponsibilityObligation remains a local,
append-only responsibility fact. This module composes them for one concrete
use without rewriting either source or granting action authority.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from portable_runtime.public_contracts.models import (
    ExperienceUseAdmissionV1,
    ExperienceUseRequirementV1,
)
from sqlalchemy import select

from app.models.responsibility import (
    ResponsibilityObligation,
    ResponsibilityObligationStatus,
)


@dataclass(frozen=True)
class CommerceCurrentUseEligibility:
    eligible: bool
    status: str
    portable_status: str
    applicable_obligation_refs: tuple[str, ...]
    reasons: tuple[str, ...]
    authority_bearing: bool = False


def _scope_applies(obligation_scope: Mapping[str, Any], use_scope: Mapping[str, Any]) -> bool:
    """An obligation applies only inside its declared Commerce scope."""

    if not obligation_scope:
        return True
    return all(
        key in use_scope and use_scope[key] == value for key, value in obligation_scope.items()
    )


def current_open_obligations_for_use(
    db,
    *,
    projection_refs: list[str],
    use_scope: Mapping[str, Any],
) -> list[ResponsibilityObligation]:
    """Resolve open obligations intersecting both Experience refs and use scope."""

    refs = {str(ref).strip() for ref in projection_refs if str(ref).strip()}
    if not refs:
        return []
    rows = db.execute(
        select(ResponsibilityObligation)
        .where(ResponsibilityObligation.status == ResponsibilityObligationStatus.OPEN)
        .order_by(ResponsibilityObligation.created_at, ResponsibilityObligation.id)
    ).scalars()
    return [
        row
        for row in rows
        if refs.intersection(set(row.projection_refs or []))
        and _scope_applies(row.scope or {}, use_scope)
    ]


def compose_current_use_eligibility(
    db,
    *,
    admission: ExperienceUseAdmissionV1,
    requirement: ExperienceUseRequirementV1,
) -> CommerceCurrentUseEligibility:
    """Compose two independent current-use gates without creating authority."""

    obligations = current_open_obligations_for_use(
        db,
        projection_refs=list(requirement.projection_refs),
        use_scope=requirement.use_scope,
    )
    obligation_refs = tuple(str(row.id) for row in obligations)
    reasons = list(admission.reasons)
    reasons.extend(f"open-responsibility-obligation:{ref}" for ref in obligation_refs)

    if admission.status != "allowed":
        status = admission.status
        eligible = False
    elif obligations:
        status = "blocked"
        eligible = False
    else:
        status = "allowed"
        eligible = True

    return CommerceCurrentUseEligibility(
        eligible=eligible,
        status=status,
        portable_status=admission.status,
        applicable_obligation_refs=obligation_refs,
        reasons=tuple(sorted(set(reasons))),
    )


__all__ = [
    "CommerceCurrentUseEligibility",
    "compose_current_use_eligibility",
    "current_open_obligations_for_use",
]
