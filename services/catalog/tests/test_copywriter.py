from catalog_app.copywriter import deterministic_copy
from catalog_app.models import CatalogItem
from catalog_app.validation import validate_draft


def test_demo_copywriter_produces_gate_valid_title_for_short_product() -> None:
    item = CatalogItem(
        run_id="run-1",
        sku="HOME-005",
        source_title="衣柜除湿袋",
        category="家居生活",
        price="24.90",
        stock=100,
        attributes="十袋装",
    )

    draft = deterministic_copy(item)

    assert validate_draft(draft) == []
    assert draft.listing_title == "衣柜除湿袋｜十袋装｜日常实用款"
