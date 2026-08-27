from __future__ import annotations

import app.workflows  # noqa: F401 - activates explicit workflow continuation aliases
from app.services import commands
from app.services.approvals import _STEP_REGISTRY


def test_return_to_refund_uses_existing_return_financial_continuations() -> None:
    """The webhook workflow must not silently drop ReturnCase continuations."""

    registry = _STEP_REGISTRY["return-to-refund"]
    assert registry["approve_credit_note"] is commands._approve_return_credit_note
    assert registry["approve_refund"] is commands._approve_return_refund
