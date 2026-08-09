"""Outbound connectors: Shopify (Admin GraphQL), Odoo (JSON-2), Metabase (read-only).

See ``app.connectors.base`` for the shared ``EffectResult`` /
``ConnectorError`` / ``OutcomeUnknownError`` contract and the sync-vs-async
decision, and the individual modules for system-specific behavior.
"""

from __future__ import annotations

from app.connectors.base import (
    EFFECT_STATUSES,
    ChannelConnector,
    ConnectorError,
    EffectResult,
    OutcomeUnknownError,
    payload_hash,
)
from app.connectors.metabase import MetabaseHealth
from app.connectors.odoo import OdooConnector
from app.connectors.registry import connector_names, get_connector
from app.connectors.shopify import ShopifyConnector

__all__ = [
    "ChannelConnector",
    "ConnectorError",
    "EFFECT_STATUSES",
    "EffectResult",
    "MetabaseHealth",
    "OdooConnector",
    "OutcomeUnknownError",
    "ShopifyConnector",
    "connector_names",
    "get_connector",
    "payload_hash",
]
