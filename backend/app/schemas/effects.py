"""Strongly typed effect execution seam (P7 WP5).

Defines the internal effect interface consumed by the DBOS workflow v2
(WP4) and produced by the connectors:

- :class:`EffectExecutionRequest` — the durable intent to execute one
  external side effect (one effect ledger row, one DBOS workflow step).
  ``parameters`` is a discriminated union: one parameter model per
  ``EFFECT_OPS`` entry, validated at construction time.
- :class:`EffectExecutionOutcome` — the typed result of executing an
  effect: ``succeeded | failed | outcome_unknown`` (discriminated union).

Fail-closed rules enforced by the seam (``services.effect_ledger``):

- Adapters never classify outcomes by string matching (no ``"timeout"``
  inference); ambiguous transport failures surface as
  ``outcome_unknown`` via :class:`OutcomeUnknownError`.
- Only ``failed(retryable=True)`` may be retried, bounded to 3 attempts.
- ``outcome_unknown`` is never auto-re-dispatched; it routes to
  ``needs_reconciliation``.
- An unconfigured adapter/operation is a startup configuration error; it
  must never be reported as ``succeeded``.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from app.schemas.events import EFFECT_OPS

# ---------------------------------------------------------------------------
# Stable error codes (never inferred from message text)
# ---------------------------------------------------------------------------

ERROR_OUTCOME_UNKNOWN = "outcome_unknown"
"""Remote state is ambiguous (transport/5xx): never blind-retried."""

ERROR_REMOTE_ERROR = "remote_error"
"""Definitive remote failure with a known, non-applied outcome."""

ERROR_RETRYABLE = "retryable_error"
"""Definitive remote failure that is safe to retry (effect not applied)."""

ERROR_EXPECTED_CONFLICT = "expected_conflict"
"""Remote returned an expected conflict (e.g. record already exists)."""

ERROR_CONNECTOR_NOT_CONFIGURED = "connector_not_configured"
"""Adapter/operation configuration error (startup concern, never succeeded)."""


class EffectParameters(BaseModel):
    """Base class for every per-operation parameter model.

    Field names mirror the adapter method keyword arguments so the seam can
    dispatch ``parameters.model_dump(exclude={"operation"})`` directly.
    ``operation`` is the discriminated-union tag and must equal
    ``EffectExecutionRequest.operation``.
    """

    model_config = ConfigDict(extra="forbid")

    operation: str


# ---------------------------------------------------------------------------
# Shopify parameter models
# ---------------------------------------------------------------------------


class ShopifyRefundCreateParams(EffectParameters):
    """``shopify.refund_create``: native ``@idempotent(key:)`` refund."""

    operation: Literal["shopify.refund_create"]
    order_gid: str
    amount: str | int | float
    note: str | None = None
    notify: bool = False
    refund_line_items: list[dict[str, Any]] | None = None
    parent_transaction_id: str | None = None
    gateway: str = "manual"
    # Fail-closed: refunds never move real money unless explicitly allowed.
    allow_real_money: bool = False


class ShopifyProductUpdateParams(EffectParameters):
    """``shopify.product_update``: read-back idempotent by product GID."""

    operation: Literal["shopify.product_update"]
    gid: str
    payload: dict[str, Any]


class ShopifyProductPublishParams(EffectParameters):
    """``shopify.product_publish``: read-back idempotent by publication."""

    operation: Literal["shopify.product_publish"]
    gid: str
    publication_id: str | None = None
    publish_date: str | None = None


class ShopifyFulfillmentCreateParams(EffectParameters):
    """``shopify.fulfillment_create``: pre-checked against existing state."""

    operation: Literal["shopify.fulfillment_create"]
    order_gid: str
    tracking: dict[str, Any] | None = None
    location_gid: str | None = None
    fulfillment_order_gid: str | None = None
    notify_customer: bool = False


# ---------------------------------------------------------------------------
# Odoo parameter models
# ---------------------------------------------------------------------------


class OdooProductCreateParams(EffectParameters):
    """``odoo.product_create``: query by ``default_code`` (SKU) before create."""

    operation: Literal["odoo.product_create"]
    values: dict[str, Any]


class OdooProductUpdateParams(EffectParameters):
    """``odoo.product_update``: write by Odoo record id."""

    operation: Literal["odoo.product_update"]
    odoo_id: int
    values: dict[str, Any]


class OdooSaleOrderCreateParams(EffectParameters):
    """``odoo.sale_order_create``: marker ``CO:<intent_id>`` in client_order_ref."""

    operation: Literal["odoo.sale_order_create"]
    values: dict[str, Any]


class OdooSaleOrderConfirmParams(EffectParameters):
    """``odoo.sale_order_confirm``: state pre-check before confirm."""

    operation: Literal["odoo.sale_order_confirm"]
    odoo_id: int


class OdooStockMoveCreateParams(EffectParameters):
    """``odoo.stock_move_create``: marker ``CO:<intent_id>`` in origin."""

    operation: Literal["odoo.stock_move_create"]
    values: dict[str, Any]


class OdooPickingCreateParams(EffectParameters):
    """``odoo.picking_create``: marker ``CO:<intent_id>`` in origin."""

    operation: Literal["odoo.picking_create"]
    values: dict[str, Any]


class OdooPickingValidateParams(EffectParameters):
    """``odoo.picking_validate``: state pre-check before validate."""

    operation: Literal["odoo.picking_validate"]
    odoo_id: int


class OdooReceiveTransferParams(EffectParameters):
    """``odoo.receive_transfer``: state pre-check before receive."""

    operation: Literal["odoo.receive_transfer"]
    odoo_id: int


class OdooInvoiceCreateParams(EffectParameters):
    """``odoo.invoice_create``: marker ``CO:<intent_id>`` in ref."""

    operation: Literal["odoo.invoice_create"]
    values: dict[str, Any]


class OdooInvoiceValidateParams(EffectParameters):
    """``odoo.invoice_validate``: state pre-check before post."""

    operation: Literal["odoo.invoice_validate"]
    odoo_id: int


class OdooCreditNoteCreateParams(EffectParameters):
    """``odoo.credit_note_create``: marker ``CO:<intent_id>`` in ref."""

    operation: Literal["odoo.credit_note_create"]
    values: dict[str, Any]


class OdooCreditNoteValidateParams(EffectParameters):
    """``odoo.credit_note_validate``: state pre-check before post."""

    operation: Literal["odoo.credit_note_validate"]
    odoo_id: int


class OdooPoCreateParams(EffectParameters):
    """``odoo.po_create``: marker ``CO:<intent_id>`` in partner_ref."""

    operation: Literal["odoo.po_create"]
    values: dict[str, Any]


class OdooPoConfirmParams(EffectParameters):
    """``odoo.po_confirm``: state pre-check before confirm."""

    operation: Literal["odoo.po_confirm"]
    odoo_id: int


class OdooBillCreateParams(EffectParameters):
    """``odoo.bill_create``: marker ``CO:<intent_id>`` in ref."""

    operation: Literal["odoo.bill_create"]
    values: dict[str, Any]


EFFECT_PARAMETER_MODELS: dict[str, type[EffectParameters]] = {
    "shopify.refund_create": ShopifyRefundCreateParams,
    "shopify.product_update": ShopifyProductUpdateParams,
    "shopify.product_publish": ShopifyProductPublishParams,
    "shopify.fulfillment_create": ShopifyFulfillmentCreateParams,
    "odoo.product_create": OdooProductCreateParams,
    "odoo.product_update": OdooProductUpdateParams,
    "odoo.sale_order_create": OdooSaleOrderCreateParams,
    "odoo.sale_order_confirm": OdooSaleOrderConfirmParams,
    "odoo.stock_move_create": OdooStockMoveCreateParams,
    "odoo.picking_create": OdooPickingCreateParams,
    "odoo.picking_validate": OdooPickingValidateParams,
    "odoo.receive_transfer": OdooReceiveTransferParams,
    "odoo.invoice_create": OdooInvoiceCreateParams,
    "odoo.invoice_validate": OdooInvoiceValidateParams,
    "odoo.credit_note_create": OdooCreditNoteCreateParams,
    "odoo.credit_note_validate": OdooCreditNoteValidateParams,
    "odoo.po_create": OdooPoCreateParams,
    "odoo.po_confirm": OdooPoConfirmParams,
    "odoo.bill_create": OdooBillCreateParams,
}
"""Every ``EFFECT_OPS`` operation maps to exactly one parameter model."""


EffectParametersUnion = Annotated[
    Union[  # noqa: UP007 - 19-member discriminated union stays explicit
        ShopifyRefundCreateParams,
        ShopifyProductUpdateParams,
        ShopifyProductPublishParams,
        ShopifyFulfillmentCreateParams,
        OdooProductCreateParams,
        OdooProductUpdateParams,
        OdooSaleOrderCreateParams,
        OdooSaleOrderConfirmParams,
        OdooStockMoveCreateParams,
        OdooPickingCreateParams,
        OdooPickingValidateParams,
        OdooReceiveTransferParams,
        OdooInvoiceCreateParams,
        OdooInvoiceValidateParams,
        OdooCreditNoteCreateParams,
        OdooCreditNoteValidateParams,
        OdooPoCreateParams,
        OdooPoConfirmParams,
        OdooBillCreateParams,
    ],
    Field(discriminator="operation"),
]
"""Discriminated union of all per-operation parameter models."""


def validate_effect_parameter_coverage() -> None:
    """Fail fast when ``EFFECT_OPS`` and the parameter models drift apart."""
    missing = EFFECT_OPS - set(EFFECT_PARAMETER_MODELS)
    if missing:
        raise RuntimeError(
            f"missing parameter models for effect operations: {sorted(missing)}"
        )


class EffectExecutionRequest(BaseModel):
    """Durable intent to execute one external side effect."""

    model_config = ConfigDict(extra="forbid")

    intent_id: UUID
    operation: str
    parameters: EffectParametersUnion
    idempotency_key: str | None = None
    request_hash: str | None = None
    correlation_id: str | None = None
    approval_ref: UUID | None = None

    @model_validator(mode="after")
    def _validate_operation(self) -> EffectExecutionRequest:
        if self.operation not in EFFECT_OPS:
            raise ValueError(f"unknown effect operation: {self.operation!r}")
        if self.operation != self.parameters.operation:
            raise ValueError(
                f"request operation {self.operation!r} does not match parameters "
                f"operation {self.parameters.operation!r}"
            )
        return self


# ---------------------------------------------------------------------------
# Typed outcomes (discriminated union)
# ---------------------------------------------------------------------------


class EffectOutcomeBase(BaseModel):
    """Base for the typed outcome union."""

    model_config = ConfigDict(extra="forbid")

    outcome: Literal["succeeded", "failed", "outcome_unknown"]


class EffectSucceeded(EffectOutcomeBase):
    """The remote effect is confirmed applied (or was already applied)."""

    outcome: Literal["succeeded"] = "succeeded"
    remote_reference: str | None = None
    response_hash: str | None = None
    replayed: bool = False


class EffectFailed(EffectOutcomeBase):
    """A definitive failure; only ``retryable=True`` may be retried."""

    outcome: Literal["failed"] = "failed"
    error_code: str
    detail: str
    retryable: bool = False
    response_hash: str | None = None


class EffectOutcomeUnknown(EffectOutcomeBase):
    """Remote state is ambiguous — never auto-re-dispatched."""

    outcome: Literal["outcome_unknown"] = "outcome_unknown"
    error_code: str = ERROR_OUTCOME_UNKNOWN
    detail: str


EffectExecutionOutcome = Annotated[
    Union[EffectSucceeded, EffectFailed, EffectOutcomeUnknown],  # noqa: UP007
    Field(discriminator="outcome"),
]
"""Typed result of executing one effect (Pydantic discriminated union)."""

_effect_outcome_adapter = TypeAdapter(EffectExecutionOutcome)


def parse_effect_outcome(data: Any) -> EffectExecutionOutcome:
    """Validate raw data into the typed outcome union (discriminated)."""
    return _effect_outcome_adapter.validate_python(data)


__all__ = [
    "ERROR_CONNECTOR_NOT_CONFIGURED",
    "ERROR_EXPECTED_CONFLICT",
    "ERROR_OUTCOME_UNKNOWN",
    "ERROR_REMOTE_ERROR",
    "ERROR_RETRYABLE",
    "EFFECT_PARAMETER_MODELS",
    "EffectExecutionOutcome",
    "EffectExecutionRequest",
    "EffectFailed",
    "EffectOutcomeBase",
    "EffectOutcomeUnknown",
    "EffectParameters",
    "EffectParametersUnion",
    "EffectSucceeded",
    "parse_effect_outcome",
    "validate_effect_parameter_coverage",
]
