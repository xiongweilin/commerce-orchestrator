import re
from decimal import Decimal, InvalidOperation

from .schemas import CopyDraft

ALLOWED_CATEGORIES = {"办公用品", "家居生活", "数码配件", "户外运动"}
FORBIDDEN_COPY = {"最便宜", "百分百", "绝对", "第一", "国家级", "永久"}
SKU_PATTERN = re.compile(r"^[A-Z0-9-]{3,32}$")


def validate_source(row: dict) -> tuple[dict, list[str]]:
    errors: list[str] = []
    sku = row.get("sku", "").strip().upper()
    source_title = row.get("source_title", "").strip()
    category = row.get("category", "").strip()
    attributes = row.get("attributes", "").strip()
    if not SKU_PATTERN.fullmatch(sku):
        errors.append("invalid_sku")
    if not source_title or len(source_title) > 200:
        errors.append("invalid_source_title")
    if category not in ALLOWED_CATEGORIES:
        errors.append("invalid_category")
    try:
        price = Decimal(row.get("price", ""))
        if not Decimal("0.01") <= price <= Decimal("100000"):
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        price = Decimal("0")
        errors.append("invalid_price")
    try:
        stock = int(row.get("stock", ""))
        if not 0 <= stock <= 999_999:
            raise ValueError
    except ValueError:
        stock = 0
        errors.append("invalid_stock")
    return {
        "sku": sku,
        "source_title": source_title,
        "category": category,
        "price": price,
        "stock": stock,
        "attributes": attributes,
    }, errors


def validate_draft(draft: CopyDraft) -> list[str]:
    errors: list[str] = []
    if not 10 <= len(draft.listing_title) <= 60:
        errors.append("listing_title_length")
    if len(set(draft.selling_points)) != 3 or any(
        not 5 <= len(point) <= 80 for point in draft.selling_points
    ):
        errors.append("invalid_selling_points")
    if len(set(draft.keywords)) != len(draft.keywords) or any(
        not 1 <= len(keyword) <= 20 for keyword in draft.keywords
    ):
        errors.append("invalid_keywords")
    text = " ".join([draft.listing_title, *draft.selling_points, *draft.keywords])
    if any(word in text for word in FORBIDDEN_COPY):
        errors.append("forbidden_claim")
    return errors
