# -*- coding: utf-8 -*-
"""Odoo XML-RPC adapter for the Catalog RPA bot.

Provides a thin, field-caching wrapper around xmlrpc.client that is
testable in isolation and hides the raw ServerProxy lifecycle.
"""

import xmlrpc.client
# -----------------------------------------------------------------------
from urllib.parse import parse_qs, urlsplit

# -----------------------------------------------------------------------
# Model name constants (avoid string-literal duplication)
# -----------------------------------------------------------------------

MODEL_PRODUCT_TEMPLATE = "product.template"
MODEL_PRODUCT_PRODUCT = "product.product"



class OdooClient:
    """Stateful Odoo XML-RPC connection with field-metadata cache."""

    def __init__(self, login_url: str, username: str, password: str):
        base_url, db = _parse_odoo_login_url(login_url)
        self.base_url = base_url
        self.db = db
        self.uid = self._authenticate(base_url, db, username, password)
        self.password = password
        self._models = xmlrpc.client.ServerProxy(
            f"{base_url}/xmlrpc/2/object", allow_none=True
        )
        self._fields_cache: dict[str, dict] = {}

    # -- public API -------------------------------------------------------

    def execute(self, model: str, method: str, args=None, kwargs=None):
        args = args or []
        kwargs = kwargs or {}
        try:
            return self._models.execute_kw(
                self.db, self.uid, self.password, model, method, args, kwargs
            )
        except xmlrpc.client.Fault as exc:
            raise RuntimeError(
                f"Odoo RPC failed: {model}.{method}: {_compact_fault(exc)}"
            )

    def fields_get(self, model: str) -> dict:
        if model in self._fields_cache:
            return self._fields_cache[model]
        fields = self.execute(
            model, "fields_get", [],
            {"attributes": ["type", "required", "readonly", "selection", "string"]},
        )
        self._fields_cache[model] = fields
        return fields

    def field_exists(self, model: str, name: str) -> bool:
        return name in self.fields_get(model)

    def field_writable(self, model: str, name: str) -> bool:
        fields = self.fields_get(model)
        if name not in fields:
            return False
        return not bool(fields[name].get("readonly"))

    @staticmethod
    def selection_keys(field_def: dict) -> list[str]:
        raw = field_def.get("selection") or []
        keys = []
        for item in raw:
            if isinstance(item, (list, tuple)) and item:
                keys.append(str(item[0]))
            else:
                keys.append(str(item))
        return keys

    # -- product lookup helpers -------------------------------------------

    def search_template_by_sku(self, sku: str) -> int | None:
        if not self.field_exists(MODEL_PRODUCT_TEMPLATE, "default_code"):
            return None
        records = self.execute(
            MODEL_PRODUCT_TEMPLATE, "search_read",
            [[["default_code", "=", sku]]],
            {"fields": ["id", "name", "default_code"], "limit": 1},
        )
        return records[0]["id"] if records else None

    def search_variant_template_id(self, sku: str) -> int | None:
        try:
            self.fields_get(MODEL_PRODUCT_PRODUCT)
        except Exception:
            return None
        if not self.field_exists(MODEL_PRODUCT_PRODUCT, "default_code"):
            return None
        records = self.execute(
            MODEL_PRODUCT_PRODUCT, "search_read",
            [[["default_code", "=", sku]]],
            {"fields": ["id", "product_tmpl_id", "default_code"], "limit": 1},
        )
        if not records:
            return None
        product_tmpl_id = records[0].get("product_tmpl_id")
        if isinstance(product_tmpl_id, (list, tuple)) and product_tmpl_id:
            return product_tmpl_id[0]
        return None

    def product_exists(self, sku: str) -> int | None:
        product_id = self.search_template_by_sku(sku)
        if product_id:
            return product_id
        return self.search_variant_template_id(sku)

    # -- internals --------------------------------------------------------

    @staticmethod
    def _authenticate(base_url, db, username, password):
        common = xmlrpc.client.ServerProxy(
            f"{base_url}/xmlrpc/2/common", allow_none=True
        )
        uid = common.authenticate(db, username, password, {})
        if not uid:
            raise RuntimeError(f"odoo authentication failed for user {username}")
        return uid


# -----------------------------------------------------------------------
# helpers (module-level utilities kept here so this module stays
# self-contained)
# -----------------------------------------------------------------------

def _parse_odoo_login_url(odoo_login_url: str) -> tuple[str, str]:
    parsed = urlsplit(odoo_login_url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"invalid odoo_login_url: {odoo_login_url}")
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    query = parse_qs(parsed.query or "")
    db = ""
    if "db" in query and query["db"]:
        db = query["db"][0]
    if not db:
        raise ValueError(f"missing db in odoo_login_url: {odoo_login_url}")
    return base_url, db


def _compact_fault(exc) -> str:
    text = str(getattr(exc, "faultString", "") or exc)
    text = text.replace("\\n", "\n")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    important = []
    for line in lines:
        if (
            line.startswith("ValueError:")
            or line.startswith("KeyError:")
            or line.startswith("TypeError:")
            or line.startswith("UserError:")
            or line.startswith("ValidationError:")
            or line.startswith("AccessError:")
            or "odoo.exceptions" in line
            or "Wrong value" in line
            or "Invalid field" in line
            or "required field" in line
            or "Missing required" in line
        ):
            important.append(line)
    if important:
        return " | ".join(important[-4:])[:900]
    if lines:
        return " | ".join(lines[-6:])[:900]
    return str(exc)[:900]
