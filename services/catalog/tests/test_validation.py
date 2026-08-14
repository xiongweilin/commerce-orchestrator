from catalog_app.schemas import CopyDraft
from catalog_app.validation import validate_draft, validate_source


def test_source_validation_normalizes_and_accepts_valid_row() -> None:
    values, errors = validate_source(
        {
            "sku": " desk-001 ",
            "source_title": "桌面收纳盒",
            "category": "办公用品",
            "price": "39.90",
            "stock": "25",
            "attributes": "白色，大号",
        }
    )
    assert errors == []
    assert values["sku"] == "DESK-001"
    assert str(values["price"]) == "39.90"


def test_source_validation_collects_all_deterministic_errors() -> None:
    _, errors = validate_source(
        {
            "sku": "?",
            "source_title": "",
            "category": "未知",
            "price": "-1",
            "stock": "many",
            "attributes": "",
        }
    )
    assert set(errors) == {
        "invalid_sku",
        "invalid_source_title",
        "invalid_category",
        "invalid_price",
        "invalid_stock",
    }


def test_copy_validation_rejects_forbidden_claim_and_duplicate_points() -> None:
    draft = CopyDraft(
        listing_title="百分百耐用的桌面收纳盒",
        selling_points=["适合桌面整理", "适合桌面整理", "白色大号设计"],
        keywords=["收纳", "办公", "桌面"],
    )
    assert set(validate_draft(draft)) == {"invalid_selling_points", "forbidden_claim"}
