"""Connector registry: lazy singleton access by name.

Construction reads settings only (no network I/O at import or on
``get_connector``); each connector creates its ``httpx.Client`` lazily on
its first request, so importing this module never touches the network.
"""

from __future__ import annotations

from app.config import Settings, get_settings
from app.connectors.base import ChannelConnector, ConnectorError
from app.connectors.metabase import MetabaseHealth
from app.connectors.odoo import OdooConnector
from app.connectors.shopify import ShopifyConnector

_CONNECTOR_NAMES = ("shopify", "odoo", "metabase")
_SINGLETONS: dict[str, ChannelConnector] = {}


def connector_names() -> list[str]:
    """Return the available connector names in stable order."""
    return list(_CONNECTOR_NAMES)


def _build(name: str) -> ChannelConnector:
    settings: Settings = get_settings()
    if name == "shopify":
        return ShopifyConnector(settings)
    if name == "odoo":
        return OdooConnector(settings)
    if name == "metabase":
        return MetabaseHealth(settings)
    raise ConnectorError(f"unknown connector {name!r}; available: {', '.join(_CONNECTOR_NAMES)}")


def get_connector(name: str) -> ChannelConnector:
    """Return the configured singleton connector for ``name`` (lazy)."""
    key = name.strip().lower()
    if key not in _SINGLETONS:
        _SINGLETONS[key] = _build(key)
    return _SINGLETONS[key]


__all__ = ["connector_names", "get_connector"]
