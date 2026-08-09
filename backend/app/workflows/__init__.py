"""DBOS worker workflows for the first vertical slice.

``configure_dbos`` / ``start_worker`` are imported eagerly; the DBOS-decorated
workflow functions are lazy (PEP 562 ``__getattr__``) so importing this
package does not pull in ``dbos`` until a workflow function is requested.
"""

from __future__ import annotations

from app.workflows.bootstrap import configure_dbos, start_worker

_WORKFLOW_FUNCS = (
    "catalog_change_and_listing_workflow",
    "daily_reconciliation_workflow",
    "order_to_cash_workflow",
    "procurement_workflow",
    "return_to_refund_workflow",
)


def __getattr__(name: str):
    if name in _WORKFLOW_FUNCS:
        from app.workflows import vertical_slice

        return getattr(vertical_slice, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "catalog_change_and_listing_workflow",
    "configure_dbos",
    "daily_reconciliation_workflow",
    "order_to_cash_workflow",
    "procurement_workflow",
    "return_to_refund_workflow",
    "start_worker",
]
