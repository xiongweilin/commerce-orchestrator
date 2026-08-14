# -*- coding: utf-8 -*-
"""Product creation and field-value builder for the Catalog RPA bot.

Depends on `odoo_adapter.OdooClient` for all Odoo interaction so the
product-building logic can be tested with a fake adapter.
"""

import re

from shadowbot.odoo_adapter import OdooClient
# -----------------------------------------------------------------------
# Model name constants (avoid string-literal duplication)
# -----------------------------------------------------------------------

MODEL_PRODUCT_TEMPLATE = "product.template"
MODEL_PRODUCT_PRODUCT = "product.product"




# -----------------------------------------------------------------------
# Item helpers
# -----------------------------------------------------------------------

def _item_value(item: dict, name: str, default: str = "") -> str:
    return str(item.get(name, default) or "").strip()


# -----------------------------------------------------------------------
# Price parsing
# -----------------------------------------------------------------------

def parse_price(value) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    cleaned = re.sub(r"[^0-9.\-]", "", text)
    if not cleaned or cleaned in ("-", ".", "-."):
        return 0.0
    return float(cleaned)


# -----------------------------------------------------------------------
# Note builder
# -----------------------------------------------------------------------

def build_note(item: dict) -> str:
    return "\n".join([
        "来源：客户反馈作品集模拟商品上架流程",
        f"SKU：{_item_value(item, 'sku')}",
        f"源品类：{_item_value(item, 'category')}",
        f"源库存：{_item_value(item, 'stock')}",
        f"卖点：{_item_value(item, 'selling_points')}",
        f"关键词：{_item_value(item, 'keywords')}",
        f"追踪 item_id：{_item_value(item, 'item_id')}",
    ])


# -----------------------------------------------------------------------
# Product type detection
# -----------------------------------------------------------------------

def pick_product_type_value(odoo: OdooClient):
    fields = odoo.fields_get(MODEL_PRODUCT_TEMPLATE)
    if odoo.field_writable(MODEL_PRODUCT_TEMPLATE, "detailed_type"):
        keys = odoo.selection_keys(fields["detailed_type"])
        if "consu" in keys:
            return "detailed_type", "consu"
        if "product" in keys:
            return "detailed_type", "product"
        if keys:
            print(f"warning: detailed_type selections: {keys}")
    if odoo.field_writable(MODEL_PRODUCT_TEMPLATE, "type"):
        keys = odoo.selection_keys(fields["type"])
        if "consu" in keys:
            return "type", "consu"
        if "product" in keys:
            return "type", "product"
        if keys:
            print(f"warning: type selections: {keys}")
    return None, None


# -----------------------------------------------------------------------
# Product values builder
# -----------------------------------------------------------------------

def build_product_values(odoo: OdooClient, item: dict, mode: str) -> dict:
    """Build values dict for product.template create/write.

    The ``full`` mode enrichment is delegated to ``_enrich_full_mode_values``
    to keep the cognitive complexity of this function low.
    """
    sku = _item_value(item, "sku")
    title = _item_value(item, "listing_title")
    price = parse_price(_item_value(item, "price"))
    note = build_note(item)

    if not sku:
        raise ValueError(f"missing sku for item_id={_item_value(item, 'item_id')}")
    if not title:
        raise ValueError(f"missing listing_title for sku={sku}")

    values: dict = {"name": title}

    if mode in ("full", "no_type"):
        _set_if_writable(odoo, MODEL_PRODUCT_TEMPLATE, values, "default_code", sku)
        _set_if_writable(odoo, MODEL_PRODUCT_TEMPLATE, values, "list_price", price)

    if mode == "full":
        _enrich_full_mode_values(odoo, values, note)

    return values


def _set_if_writable(odoo: OdooClient, model: str, values: dict, field: str, value) -> None:
    """Set *value* on values dict when *field* is writable on *model*."""
    if odoo.field_writable(model, field):
        values[field] = value


def _enrich_full_mode_values(odoo: OdooClient, values: dict, note: str) -> None:
    """Add product-type, description, and sales flags (full mode only)."""
    field_name, field_value = pick_product_type_value(odoo)
    if field_name and field_value:
        values[field_name] = field_value
    for field in ("description_sale", "description"):
        _set_if_writable(odoo, MODEL_PRODUCT_TEMPLATE, values, field, note)
    for field in ("sale_ok", "purchase_ok"):
        _set_if_writable(odoo, MODEL_PRODUCT_TEMPLATE, values, field, True)


# -----------------------------------------------------------------------
# Write helpers
# -----------------------------------------------------------------------

def write_template_values(odoo: OdooClient, product_id: int, values: dict) -> bool:
    if not values:
        return False
    odoo.execute(MODEL_PRODUCT_TEMPLATE, "write", [[product_id], values])
    return True


def write_variant_sku(odoo: OdooClient, product_id: int, sku: str) -> bool:
    try:
        odoo.fields_get(MODEL_PRODUCT_PRODUCT)
    except Exception as exc:
        print(f"warning: cannot read product.product fields: {str(exc)[:300]}")
        return False
    if not odoo.field_writable(MODEL_PRODUCT_PRODUCT, "default_code"):
        return False
    variants = odoo.execute(
        MODEL_PRODUCT_PRODUCT, "search_read",
        [[["product_tmpl_id", "=", product_id]]],
        {"fields": ["id", "default_code"], "limit": 1},
    )
    if not variants:
        return False
    variant_id = variants[0]["id"]
    odoo.execute(MODEL_PRODUCT_PRODUCT, "write", [[variant_id], {"default_code": sku}])
    return True


# -----------------------------------------------------------------------
# Post-create optional field update
# -----------------------------------------------------------------------

def after_create_update_optional_fields(odoo: OdooClient, product_id: int, item: dict):
    sku = _item_value(item, "sku")
    price = parse_price(_item_value(item, "price"))
    note = build_note(item)

    values = {}
    if odoo.field_writable(MODEL_PRODUCT_TEMPLATE, "default_code"):
        values["default_code"] = sku
    if odoo.field_writable(MODEL_PRODUCT_TEMPLATE, "list_price"):
        values["list_price"] = price
    if odoo.field_writable(MODEL_PRODUCT_TEMPLATE, "description_sale"):
        values["description_sale"] = note
    if odoo.field_writable(MODEL_PRODUCT_TEMPLATE, "description"):
        values["description"] = note
    if odoo.field_writable(MODEL_PRODUCT_TEMPLATE, "sale_ok"):
        values["sale_ok"] = True
    if odoo.field_writable(MODEL_PRODUCT_TEMPLATE, "purchase_ok"):
        values["purchase_ok"] = True

    field_name, field_value = pick_product_type_value(odoo)
    if field_name and field_value and odoo.field_writable(MODEL_PRODUCT_TEMPLATE, field_name):
        values[field_name] = field_value

    try:
        write_template_values(odoo, product_id, values)
    except Exception as exc:
        print(f"warning: optional template write failed: {str(exc)[:400]}")

    try:
        write_variant_sku(odoo, product_id, sku)
    except Exception as exc:
        print(f"warning: variant sku write failed: {str(exc)[:400]}")


# -----------------------------------------------------------------------
# Product creation orchestrator
# -----------------------------------------------------------------------

def create_template_with_fallback(odoo: OdooClient, item: dict) -> int:
    attempts = [
        ("full", build_product_values(odoo, item, "full")),
        ("no_type", build_product_values(odoo, item, "no_type")),
        ("minimal", build_product_values(odoo, item, "minimal")),
    ]
    last_error = None
    for name, values in attempts:
        try:
            print(f"create attempt {name} values keys: {sorted(values.keys())}")
            product_id = odoo.execute(MODEL_PRODUCT_TEMPLATE, "create", [values], {})
            if name != "full":
                after_create_update_optional_fields(odoo, product_id, item)
            return product_id
        except Exception as exc:
            last_error = exc
            print(f"warning: create attempt {name} failed: {str(exc)[:600]}")
    raise RuntimeError(f"all product.template create attempts failed: {last_error}")


def create_product(odoo: OdooClient, item: dict) -> str:
    sku = _item_value(item, "sku")
    existing_id = odoo.product_exists(sku)
    if existing_id:
        return f"odoo-existing-{existing_id}"
    product_id = create_template_with_fallback(odoo, item)
    return f"odoo-product-{product_id}"
