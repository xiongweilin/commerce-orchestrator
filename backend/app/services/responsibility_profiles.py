"""Commerce-owned responsibility profiles for domain workflows.

Profiles compose independent responsibility requirements without granting
Decision, Authorization, or effect authority.  Portable-runtime remains the
semantic oracle for portable contracts; these profiles are Commerce policy.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from app.models.catalog import CatalogChangeCandidate, CatalogRevision
from app.models.listing import ListingPublication

ExperiencePolicy = Literal["not-required", "feedback-or-ai-derived"]


@dataclass(frozen=True)
class CommerceResponsibilityProfile:
    authorization_required: bool
    publication_qualification_required: bool
    experience_policy: ExperiencePolicy
    confirmed_outcome_required: bool


LISTING_PUBLICATION_PROFILE = CommerceResponsibilityProfile(
    authorization_required=True,
    publication_qualification_required=True,
    experience_policy="feedback-or-ai-derived",
    confirmed_outcome_required=True,
)


def listing_experience_required(db, listing: ListingPublication) -> bool:
    """Return whether this listing provenance requires Experience reliance.

    The determination is server-owned. A listing becomes Experience-bearing
    when its catalog revision is linked to a candidate derived from feedback
    evidence (``source_refs``) or an AI model (``model_id``). Missing or manual
    provenance does not fabricate Experience use.
    """

    if LISTING_PUBLICATION_PROFILE.experience_policy == "not-required":
        return False
    payload = listing.payload or {}
    raw_revision_id = payload.get("catalog_revision_id") or payload.get("revision_id")
    if raw_revision_id is None:
        return False
    try:
        revision_id = uuid.UUID(str(raw_revision_id))
    except ValueError:
        return False
    revision = db.get(CatalogRevision, revision_id)
    if revision is None or revision.candidate_id is None:
        return False
    candidate = db.get(CatalogChangeCandidate, revision.candidate_id)
    if candidate is None:
        return False
    return bool(candidate.model_id or candidate.source_refs)


__all__ = [
    "CommerceResponsibilityProfile",
    "LISTING_PUBLICATION_PROFILE",
    "listing_experience_required",
]
