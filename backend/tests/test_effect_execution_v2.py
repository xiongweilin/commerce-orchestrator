"""P7 整改第 3 点: remote entity id chain and post-effect finalization.

Covers ``build_effect_execution_request`` / ``finalize_after_effect``:

- validate effects reference the create effect's remote reference
  (stock.picking.id / account.move.id) via domain columns written back by
  finalize, falling back to the effect ledger by ``approval_ref``/operation;
- bill / credit-note state only advances after the remote effect succeeds
  (never simulated at approval time);
- ``credit_note_id`` is sourced from the effect's remote_reference, never a
  synthetic CN-* number;
- product create/update parameter construction (no pending-WP5 branch).
"""

from __future__ import annotations

import uuid

import pytest

from app.models.catalog import CatalogRevision, CatalogRevisionStatus
from app.models.effect import EffectLedgerEntry
from app.models.listing import ListingPublication, ListingStatus
from app.models.order import SalesOrder, SalesOrderStatus
from app.models.procurement import ProcurementOrder, ProcurementStatus
from app.models.returns import ReturnCase, ReturnStatus
from app.models.workflow import WorkflowRun, WorkflowRunStatus
from app.schemas.effects import EffectFailed, EffectSucceeded
from app.schemas.events import EFFECT_OPS
from app.services.approvals import create_work_item
from app.services.effect_ledger import (
    apply_outcome,
    effect_transition_context,
    mark_dispatched,
    record_effect,
)
from app.workflows.effect_execution import (
    build_effect_execution_request,
    finalize_after_effect,
)


def _run_with_item(
    db,
    *,
    workflow_type: str = "order-to-cash",
    input_json: dict | None = None,
) -> WorkflowRun:
    run = WorkflowRun(
        workflow_type=workflow_type,
        workflow_version=2,
        orchestration_engine="dbos",
        status=WorkflowRunStatus.RUNNING,
        correlation_id="corr-effect-v2",
        input_json=input_json or {},
    )
    db.add(run)
    db.flush()
    return run


def _make_order(db, run: WorkflowRun, *, shopify_id: str = "SO-1") -> SalesOrder:
    order = SalesOrder(
        order_ref=f"ORD-{shopify_id}",
        shopify_order_id=shopify_id,
        status=SalesOrderStatus.RECEIVED,
        currency="CNY",
        total="100.00",
    )
    db.add(order)
    db.flush()
    run.input_json = {"entity_id": str(order.id), "shopify_order_id": shopify_id}
    db.flush()
    return order


def _effect(db, run: WorkflowRun, operation: str) -> EffectLedgerEntry:
    entry = record_effect(
        db,
        intent_id=uuid.uuid4(),
        target_system="odoo",
        operation=operation,
        idempotency_key=f"{run.id}:{operation}",
        approval_ref=run.id,
    )
    db.flush()
    return entry


def _succeed(db, entry: EffectLedgerEntry, *, remote: str, operation: str) -> None:
    context = effect_transition_context(operation)
    mark_dispatched(db, entry.intent_id, context=context)
    apply_outcome(
        db,
        entry.intent_id,
        EffectSucceeded(remote_reference=remote),
        context=context,
    )


def _request(db, run: WorkflowRun, operation: str):
    return build_effect_execution_request(
        db,
        run,
        {"effect_id": str(uuid.uuid4()), "operation": operation},
    )


# ---------------------------------------------------------------------------
# picking / invoice validate read the remote entity id, not the sale order id
# ---------------------------------------------------------------------------


def test_picking_validate_reads_odoo_picking_id(db) -> None:
    run = _run_with_item(db)
    order = _make_order(db, run)
    order.odoo_picking_id = "77"
    order.odoo_sale_order_id = "66"  # must not be used for stock.picking
    db.flush()

    request = _request(db, run, "odoo.picking_validate")
    assert request.parameters.odoo_id == 77


def test_picking_validate_falls_back_to_ledger_remote_reference(db) -> None:
    run = _run_with_item(db)
    order = _make_order(db, run)
    entry = _effect(db, run, "picking_create")
    _succeed(db, entry, remote="88", operation="odoo.picking_create")

    request = _request(db, run, "odoo.picking_validate")
    assert request.parameters.odoo_id == 88
    assert order.odoo_picking_id is None  # column not yet written back


def test_picking_validate_fails_closed_without_picking_id(db) -> None:
    run = _run_with_item(db)
    _make_order(db, run)
    with pytest.raises(ValueError, match="stock.picking id unknown"):
        _request(db, run, "odoo.picking_validate")


def test_invoice_validate_reads_odoo_invoice_id(db) -> None:
    run = _run_with_item(db)
    order = _make_order(db, run)
    order.odoo_invoice_id = "99"
    order.odoo_sale_order_id = "66"  # must not be used for account.move
    db.flush()

    request = _request(db, run, "odoo.invoice_validate")
    assert request.parameters.odoo_id == 99


def test_invoice_validate_falls_back_to_ledger_remote_reference(db) -> None:
    run = _run_with_item(db)
    _make_order(db, run)
    entry = _effect(db, run, "invoice_create")
    _succeed(db, entry, remote="101", operation="odoo.invoice_create")

    request = _request(db, run, "odoo.invoice_validate")
    assert request.parameters.odoo_id == 101


def test_invoice_validate_fails_closed_without_invoice_id(db) -> None:
    run = _run_with_item(db)
    _make_order(db, run)
    with pytest.raises(ValueError, match="account.move id unknown"):
        _request(db, run, "odoo.invoice_validate")


# ---------------------------------------------------------------------------
# finalize writes back remote entity ids
# ---------------------------------------------------------------------------


def test_finalize_writes_back_picking_and_invoice_ids(db) -> None:
    run = _run_with_item(db)
    order = _make_order(db, run)

    finalize_after_effect(db, run, "odoo.picking_create", EffectSucceeded(remote_reference="90"))
    finalize_after_effect(db, run, "odoo.invoice_create", EffectSucceeded(remote_reference="91"))
    db.flush()
    db.refresh(order)
    assert order.odoo_picking_id == "90"
    assert order.odoo_invoice_id == "91"


def test_sale_order_confirm_finalize_advances_confirmed(db) -> None:
    run = _run_with_item(db)
    order = _make_order(db, run)
    order.status = SalesOrderStatus.ODO_DRAFTED
    db.flush()

    finalize_after_effect(
        db,
        run,
        "odoo.sale_order_confirm",
        EffectSucceeded(remote_reference="66"),
    )
    db.refresh(order)
    assert order.status.value == "confirmed"
    assert order.odoo_sale_order_id == "66"


# ---------------------------------------------------------------------------
# bill / credit-note: effect-gated advancement (P7 整改第 2 点)
# ---------------------------------------------------------------------------


def test_bill_create_builds_po_values_and_finalize_advances_after_success(db) -> None:
    run = _run_with_item(db, workflow_type="procurement")
    po = ProcurementOrder(
        sku="SKU-1",
        qty="2",
        uom="unit",
        supplier="ACME",
        unit_cost="5.00",
        currency="CNY",
        status=ProcurementStatus.RECEIVED,
    )
    db.add(po)
    db.flush()
    create_work_item(
        db,
        workflow_id=run.id,
        kind="approval",
        title="Post bill",
        required_roles=["accountant"],
        payload={"po_id": str(po.id)},
    )

    request = _request(db, run, "odoo.bill_create")
    assert request.parameters.values["sku"] == "SKU-1"
    assert request.parameters.values["supplier"] == "ACME"

    # A failed remote effect must not advance bill_posted / in_payment.
    finalize_after_effect(
        db,
        run,
        "odoo.bill_create",
        EffectFailed(error_code="remote_error", detail="nope", retryable=False),
    )
    db.refresh(po)
    assert po.status.value == "received"
    assert po.odoo_bill_id is None

    finalize_after_effect(db, run, "odoo.bill_create", EffectSucceeded(remote_reference="4201"))
    db.refresh(po)
    assert po.status.value == "in_payment"
    assert po.odoo_bill_id == "4201"


def test_credit_note_create_finalize_advances_and_sources_number(db) -> None:
    run = _run_with_item(db, workflow_type="return-to-refund")
    case = ReturnCase(
        return_ref="RET-2",
        shopify_order_id="SO-2",
        order_ref="ORD-2",
        customer_ref="pii:abc",
        reason="damaged",
        status=ReturnStatus.DISPOSITION_APPROVED,
        refund_amount="10.00",
        currency="CNY",
    )
    db.add(case)
    db.flush()
    create_work_item(
        db,
        workflow_id=run.id,
        kind="approval",
        title="Post credit note",
        required_roles=["accountant"],
        payload={"case_id": str(case.id), "invoice_posted": True},
    )

    request = _request(db, run, "odoo.credit_note_create")
    assert request.parameters.values["invoice_origin"] == "ORD-2"
    assert request.parameters.values["currency"] == "CNY"

    finalize_after_effect(
        db,
        run,
        "odoo.credit_note_create",
        EffectFailed(error_code="remote_error", detail="nope", retryable=False),
    )
    db.refresh(case)
    assert case.status.value == "disposition_approved"
    assert case.credit_note_id is None

    finalize_after_effect(
        db,
        run,
        "odoo.credit_note_create",
        EffectSucceeded(remote_reference="502"),
    )
    db.refresh(case)
    assert case.status.value == "credit_note_posted"
    assert case.credit_note_id == "502"  # remote reference, not CN-<uuid>
    assert case.odoo_credit_note_id == "502"


def test_credit_note_validate_reads_create_remote_reference(db) -> None:
    run = _run_with_item(db, workflow_type="return-to-refund")
    case = ReturnCase(
        return_ref="RET-3",
        shopify_order_id="SO-3",
        customer_ref="pii:def",
        reason="damaged",
        status=ReturnStatus.CREDIT_NOTE_POSTED,
        refund_amount="5.00",
        currency="CNY",
    )
    db.add(case)
    db.flush()
    create_work_item(
        db,
        workflow_id=run.id,
        kind="approval",
        title="Post credit note",
        required_roles=["accountant"],
        payload={"case_id": str(case.id)},
    )
    entry = _effect(db, run, "credit_note_create")
    _succeed(db, entry, remote="503", operation="odoo.credit_note_create")

    request = _request(db, run, "odoo.credit_note_validate")
    assert request.parameters.odoo_id == 503


def test_credit_note_validate_fails_closed_without_remote_id(db) -> None:
    run = _run_with_item(db, workflow_type="return-to-refund")
    case = ReturnCase(
        return_ref="RET-4",
        shopify_order_id="SO-4",
        customer_ref="pii:ghi",
        reason="damaged",
        status=ReturnStatus.DISPOSITION_APPROVED,
        refund_amount="5.00",
        currency="CNY",
    )
    db.add(case)
    db.flush()
    create_work_item(
        db,
        workflow_id=run.id,
        kind="approval",
        title="Post credit note",
        required_roles=["accountant"],
        payload={"case_id": str(case.id)},
    )
    with pytest.raises(ValueError, match="account.move id unknown"):
        _request(db, run, "odoo.credit_note_validate")


# ---------------------------------------------------------------------------
# product create/update parameter construction (no pending-WP5 branch)
# ---------------------------------------------------------------------------


def test_product_create_builds_from_revision(db) -> None:
    run = _run_with_item(db, workflow_type="catalog-revision")
    revision = CatalogRevision(
        sku="SKU-X",
        title="Widget",
        status=CatalogRevisionStatus.APPROVED,
        proposed={"title": "Widget v2"},
    )
    db.add(revision)
    db.flush()
    create_work_item(
        db,
        workflow_id=run.id,
        kind="approval",
        title="Approve",
        required_roles=["catalog_owner"],
        payload={"revision_id": str(revision.id)},
    )

    request = _request(db, run, "odoo.product_create")
    assert request.parameters.values["default_code"] == "SKU-X"
    assert request.parameters.values["name"] == "Widget"


def test_product_update_reads_create_remote_reference(db) -> None:
    run = _run_with_item(db, workflow_type="catalog-revision")
    revision = CatalogRevision(
        sku="SKU-Y",
        title="Gadget",
        status=CatalogRevisionStatus.APPROVED,
        proposed={"title": "Gadget v2"},
    )
    db.add(revision)
    db.flush()
    create_work_item(
        db,
        workflow_id=run.id,
        kind="approval",
        title="Approve",
        required_roles=["catalog_owner"],
        payload={"revision_id": str(revision.id)},
    )
    entry = _effect(db, run, "product_create")
    _succeed(db, entry, remote="600", operation="odoo.product_create")

    request = _request(db, run, "odoo.product_update")
    assert request.parameters.odoo_id == 600
    assert request.parameters.values["title"] == "Gadget v2"


def test_product_update_fails_closed_without_remote_id(db) -> None:
    run = _run_with_item(db, workflow_type="catalog-revision")
    with pytest.raises(ValueError, match="odoo product id unknown"):
        _request(db, run, "odoo.product_update")


# ---------------------------------------------------------------------------
# Parameter construction matrix: every EFFECT_OPS builds a typed request from
# real domain rows (P7 整改 §六.1).  Validate-class operations must consume the
# create effect's ``remote_reference`` recorded on the ledger (or its domain
# write-back) rather than a synthetic id.
# ---------------------------------------------------------------------------

_CATALOG_MATRIX_OPS = {
    "shopify.product_publish",
    "shopify.product_update",
    "odoo.product_create",
    "odoo.product_update",
}
_ORDER_MATRIX_OPS = {
    "shopify.fulfillment_create",
    "odoo.sale_order_create",
    "odoo.sale_order_confirm",
    "odoo.picking_create",
    "odoo.picking_validate",
    "odoo.invoice_create",
    "odoo.invoice_validate",
}
_PROCUREMENT_MATRIX_OPS = {
    "odoo.po_create",
    "odoo.po_confirm",
    "odoo.bill_create",
    "odoo.receive_transfer",
}
_RETURN_MATRIX_OPS = {
    "shopify.refund_create",
    "odoo.credit_note_create",
    "odoo.credit_note_validate",
}


def _seed_matrix_domain(db, operation: str) -> WorkflowRun:
    """Create the minimal real domain rows one operation needs.

    Also records the create-effect ledger rows (``succeeded`` with a remote
    reference) that the validate-class builders consume, mirroring what the
    workflow driver performs before building the validate request.
    """
    if operation in _CATALOG_MATRIX_OPS:
        run = _run_with_item(db, workflow_type="catalog-revision")
        revision = CatalogRevision(
            sku="SKU-M",
            title="Matrix Widget",
            status=CatalogRevisionStatus.APPROVED,
            proposed={"title": "Matrix Widget v2", "price": "9.90"},
        )
        db.add(revision)
        db.flush()
        listing = ListingPublication(
            sku=revision.sku,
            channel="shopify",
            status=ListingStatus.PUBLISHING,
            shopify_product_gid="gid://shopify/Product/SKU-M",
            payload={"revision_id": str(revision.id)},
        )
        db.add(listing)
        db.flush()
        create_work_item(
            db,
            workflow_id=run.id,
            kind="approval",
            title="Approve revision",
            required_roles=["catalog_owner"],
            payload={"revision_id": str(revision.id)},
        )
        if operation == "odoo.product_update":
            entry = _effect(db, run, "product_create")
            _succeed(db, entry, remote="777", operation="odoo.product_create")
        return run
    if operation in _ORDER_MATRIX_OPS:
        run = _run_with_item(db, workflow_type="order-to-cash")
        order = _make_order(db, run, shopify_id="SO-M")
        if operation == "odoo.sale_order_confirm":
            entry = _effect(db, run, "sale_order_create")
            _succeed(db, entry, remote="701", operation="odoo.sale_order_create")
        elif operation == "odoo.picking_validate":
            entry = _effect(db, run, "picking_create")
            _succeed(db, entry, remote="702", operation="odoo.picking_create")
        elif operation == "odoo.invoice_validate":
            entry = _effect(db, run, "invoice_create")
            _succeed(db, entry, remote="703", operation="odoo.invoice_create")
        elif operation in {"odoo.picking_create", "odoo.invoice_create"}:
            entry = _effect(db, run, "sale_order_create")
            _succeed(db, entry, remote="701", operation="odoo.sale_order_create")
            order.odoo_sale_order_id = "701"
            db.flush()
        return run
    if operation in _PROCUREMENT_MATRIX_OPS:
        run = _run_with_item(db, workflow_type="procurement")
        po = ProcurementOrder(
            sku="SKU-P",
            qty="3",
            uom="unit",
            supplier="ACME",
            unit_cost="2.00",
            currency="CNY",
            status=ProcurementStatus.PO_CONFIRMED,
        )
        db.add(po)
        db.flush()
        create_work_item(
            db,
            workflow_id=run.id,
            kind="approval",
            title="Approve PO",
            required_roles=["budget_owner"],
            payload={"po_id": str(po.id)},
        )
        if operation in {"odoo.po_confirm", "odoo.receive_transfer"}:
            # These builders read the domain column written back by
            # finalize_after_effect when odoo.po_create succeeds; record the
            # ledger row and mirror its remote reference on the PO so the
            # assertion proves the odoo id comes from the create effect.
            entry = _effect(db, run, "po_create")
            _succeed(db, entry, remote="704", operation="odoo.po_create")
            po.odoo_po_id = "704"
            db.flush()
        return run
    if operation in _RETURN_MATRIX_OPS:
        run = _run_with_item(db, workflow_type="return-to-refund")
        case = ReturnCase(
            return_ref="RET-M",
            shopify_order_id="SO-M",
            order_ref="ORD-M",
            customer_ref="pii:matrix",
            reason="damaged",
            status=ReturnStatus.DISPOSITION_APPROVED,
            refund_amount="7.50",
            currency="CNY",
        )
        db.add(case)
        db.flush()
        create_work_item(
            db,
            workflow_id=run.id,
            kind="approval",
            title="Approve credit note",
            required_roles=["accountant"],
            payload={"case_id": str(case.id)},
        )
        if operation == "odoo.credit_note_validate":
            entry = _effect(db, run, "credit_note_create")
            _succeed(db, entry, remote="705", operation="odoo.credit_note_create")
        return run
    if operation == "odoo.stock_move_create":
        run = _run_with_item(db, workflow_type="order-to-cash")
        _make_order(db, run, shopify_id="SO-STOCK")
        return run
    raise AssertionError(f"matrix seeding not defined for {operation}")


@pytest.mark.parametrize("operation", sorted(EFFECT_OPS))
def test_effect_parameter_matrix_builds_typed_request(db, operation) -> None:
    """Every EFFECT_OPS builds a typed request with the correct parameters."""
    run = _seed_matrix_domain(db, operation)
    request = _request(db, run, operation)
    assert request.operation == operation
    assert request.parameters.operation == operation
    assert request.approval_ref == run.id

    if operation == "shopify.product_publish":
        assert request.parameters.gid == "gid://shopify/Product/SKU-M"
    elif operation == "shopify.product_update":
        assert request.parameters.gid == "gid://shopify/Product/SKU-M"
        assert request.parameters.payload == {
            "title": "Matrix Widget v2",
            "price": "9.90",
        }
    elif operation == "shopify.fulfillment_create":
        assert request.parameters.order_gid == "gid://shopify/Order/SO-M"
    elif operation == "shopify.refund_create":
        assert request.parameters.order_gid == "gid://shopify/Order/SO-M"
        assert request.parameters.amount == 7.50
        assert request.parameters.allow_real_money is False
    elif operation == "odoo.product_create":
        assert request.parameters.values["default_code"] == "SKU-M"
        assert request.parameters.values["name"] == "Matrix Widget"
    elif operation == "odoo.product_update":
        assert request.parameters.odoo_id == 777  # create effect's remote ref
        assert request.parameters.values == {
            "title": "Matrix Widget v2",
            "price": "9.90",
        }
    elif operation == "odoo.sale_order_create":
        assert request.parameters.values["items"] == []
        assert request.parameters.values["currency"] == "CNY"
        assert request.parameters.values["total"] == "100.00"
        assert request.parameters.values["partner_name"] == "Shopify Customer"
    elif operation == "odoo.sale_order_confirm":
        assert request.parameters.odoo_id == 701  # create effect's remote ref
    elif operation == "odoo.stock_move_create":
        assert request.parameters.values == {"source": "commerce-orchestrator"}
    elif operation == "odoo.picking_create":
        assert request.parameters.values == {"sale_order_id": 701}
    elif operation == "odoo.picking_validate":
        assert request.parameters.odoo_id == 702  # create effect's remote ref
    elif operation == "odoo.invoice_create":
        assert request.parameters.values == {"sale_order_id": 701, "order_ref": "ORD-SO-M"}
    elif operation == "odoo.invoice_validate":
        assert request.parameters.odoo_id == 703  # create effect's remote ref
    elif operation == "odoo.credit_note_create":
        assert request.parameters.values["invoice_origin"] == "ORD-M"
        assert request.parameters.values["currency"] == "CNY"
        assert request.parameters.values["amount"] == "7.50"
    elif operation == "odoo.credit_note_validate":
        assert request.parameters.odoo_id == 705  # create effect's remote ref
    elif operation == "odoo.po_create":
        assert request.parameters.values["sku"] == "SKU-P"
        assert request.parameters.values["supplier"] == "ACME"
        assert request.parameters.values["qty"] == "3.00"
    elif operation in {"odoo.po_confirm", "odoo.receive_transfer"}:
        assert request.parameters.odoo_id == 704  # create effect's remote ref
    elif operation == "odoo.bill_create":
        assert request.parameters.values["sku"] == "SKU-P"
        assert request.parameters.values["currency"] == "CNY"
    else:
        raise AssertionError(f"matrix assertion not defined for {operation}")


@pytest.mark.parametrize(
    "operation",
    [
        "shopify.product_publish",
        "shopify.product_update",
        "shopify.fulfillment_create",
        "shopify.refund_create",
        "odoo.product_update",
        "odoo.sale_order_confirm",
        "odoo.po_confirm",
        "odoo.receive_transfer",
    ],
)
def test_effect_parameter_matrix_fails_closed_without_domain_data(
    db, operation
) -> None:
    """Missing required domain identity raises instead of faking success."""
    run = _run_with_item(db)
    with pytest.raises(ValueError):
        _request(db, run, operation)


__all__ = []
