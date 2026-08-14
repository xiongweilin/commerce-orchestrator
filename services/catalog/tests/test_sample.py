from collections import Counter

from catalog_app.validation import validate_source
from tools.generate_sample_catalog import INVALID, VALID


def as_row(values: tuple[str, ...]) -> dict:
    return dict(
        zip(
            ["sku", "source_title", "category", "price", "stock", "attributes"],
            values,
            strict=True,
        )
    )


def test_sample_has_twenty_valid_and_ten_exception_rows() -> None:
    assert len(VALID) == 20
    assert len(INVALID) == 10
    assert all(not validate_source(as_row(row))[1] for row in VALID)
    errors = [validate_source(as_row(row))[1] for row in INVALID]
    direct_invalid = sum(bool(value) for value in errors)
    duplicate_counts = Counter(row[0] for row in INVALID)
    duplicate_only = sum(
        not value and duplicate_counts[row[0]] > 1
        for row, value in zip(INVALID, errors, strict=True)
    )
    assert direct_invalid + duplicate_only == 10


def test_dify_workflow_keeps_side_effect_fields_out_of_contract() -> None:
    text = open(
        "dify-workflows/catalog-copy-v1-candidate.yml", encoding="utf-8"
    ).read()
    assert "catalog_copy_json" in text
    assert 'EXPECTED = {"listing_title", "selling_points", "keywords"}' in text
    assert "不得输出价格、库存、审核状态、ERP ID、上架动作" in text
