"""Workflow-type aliases for shared domain approval continuations.

`return` and `return-to-refund` are distinct DBOS workflow types, but they
intentionally share the same ReturnCase continuation callbacks.  The approval
resolver is strict on workflow_type, so the webhook workflow must register the
same callbacks explicitly rather than relying on an implicit alias.
"""

from __future__ import annotations

from app.services.approvals import register_next_step
from app.services.commands import (
    _approve_return_credit_note,
    _approve_return_disposition,
    _approve_return_eligibility,
    _approve_return_refund,
    _confirm_return_received,
)

_RETURN_TO_REFUND_CONTINUATIONS = {
    "approve_eligibility": _approve_return_eligibility,
    "confirm_receipt": _confirm_return_received,
    "approve_disposition": _approve_return_disposition,
    "approve_credit_note": _approve_return_credit_note,
    "approve_refund": _approve_return_refund,
}


def register_return_to_refund_continuations() -> None:
    for step, callback in _RETURN_TO_REFUND_CONTINUATIONS.items():
        register_next_step("return-to-refund", step, callback)


register_return_to_refund_continuations()

__all__ = ["register_return_to_refund_continuations"]
