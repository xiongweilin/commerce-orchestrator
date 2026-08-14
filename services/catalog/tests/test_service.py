import io

from catalog_app.config import Settings
from catalog_app.models import CatalogItem, ERPOperation
from catalog_app.schemas import ERPResultBatch
from catalog_app.service import approved_csv, import_catalog, record_erp_results, run_payload


def csv_bytes(rows: list[str]) -> bytes:
    header = "sku,source_title,category,price,stock,attributes\n"
    return (header + "\n".join(rows) + "\n").encode()


def test_import_is_idempotent_and_duplicate_skus_are_blocked(db) -> None:
    settings = Settings(database_url="sqlite://")
    content = csv_bytes(
        [
            "BOX-001,桌面收纳盒,办公用品,39.90,20,白色",
            "BOX-001,另一个收纳盒,办公用品,49.90,10,黑色",
            "CABLE-001,编织数据线,数码配件,29.90,30,一米",
        ]
    )
    run, reused = import_catalog(db, settings, content, "products.csv", "import-001")
    same, second_reused = import_catalog(db, settings, content, "products.csv", "import-001")
    payload = run_payload(db, run)
    assert reused is False
    assert second_reused is True
    assert same.id == run.id
    assert payload["counts"] == {"imported": 1, "validation_failed": 2}


def test_approved_csv_and_erp_callback_are_idempotent(db) -> None:
    settings = Settings(database_url="sqlite://")
    run, _ = import_catalog(
        db,
        settings,
        csv_bytes(["CABLE-001,编织数据线,数码配件,29.90,30,一米"]),
        "products.csv",
        "import-002",
    )
    item = db.query(CatalogItem).one()
    item.status = "ready_for_rpa"
    item.listing_title = "耐弯折一米编织数据连接线"
    item.selling_points = ["编织线身更耐弯折", "一米长度日常适用", "接口信息清晰易选购"]
    item.keywords = ["数据线", "编织", "数码配件"]
    db.commit()
    output = approved_csv(db, run.id)
    assert "CABLE-001" in output
    assert io.StringIO(output).readline().startswith("item_id,sku")

    batch = ERPResultBatch.model_validate(
        {
            "results": [
                {
                    "item_id": item.id,
                    "operation_key": "shadowbot-CABLE-001-001",
                    "result": "written",
                    "erp_record_id": "odoo-product-42",
                }
            ]
        }
    )
    first = record_erp_results(db, run.id, batch)
    second = record_erp_results(db, run.id, batch)
    db.refresh(item)
    assert first[0]["reused"] is False
    assert second[0]["reused"] is True
    assert item.status == "erp_written"
    assert db.query(ERPOperation).count() == 1
