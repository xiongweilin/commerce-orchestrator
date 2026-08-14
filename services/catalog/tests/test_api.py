from fastapi.testclient import TestClient

from catalog_app.config import Settings, get_settings
from catalog_app.database import get_db
from catalog_app.main import app
from catalog_app.models import CatalogItem


def test_rpa_exports_require_token(db) -> None:
    app.dependency_overrides[get_db] = lambda: (yield db)
    app.dependency_overrides[get_settings] = lambda: Settings(
        database_url="sqlite://", rpa_token="test-rpa-token"
    )
    client = TestClient(app)
    response = client.post(
        "/v1/catalog/imports",
        headers={"Idempotency-Key": "api-import-001"},
        files={
            "file": (
                "products.csv",
                "sku,source_title,category,price,stock,attributes\n"
                "BOX-001,桌面收纳盒,办公用品,39.90,20,白色\n",
                "text/csv",
            )
        },
    )
    run_id = response.json()["run_id"]
    item = db.query(CatalogItem).one()
    item.status = "ready_for_rpa"
    item.listing_title = "白色大号桌面文件分类收纳盒"
    item.selling_points = ["桌面文件分类整理", "白色外观简洁耐看", "大号容量日常实用"]
    item.keywords = ["收纳盒", "办公用品", "桌面整理"]
    db.commit()
    assert client.get(f"/v1/catalog/runs/{run_id}/approved.csv").status_code == 401
    assert (
        client.get(
            f"/v1/catalog/runs/{run_id}/approved.csv",
            headers={"X-RPA-Token": "test-rpa-token"},
        ).status_code
        == 200
    )
    app.dependency_overrides.clear()
