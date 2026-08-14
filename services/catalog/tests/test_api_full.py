from fastapi.testclient import TestClient
from catalog_app.config import Settings, get_settings
from catalog_app.database import get_db
from catalog_app.main import app
from catalog_app.models import CatalogRun


def test_health(db):
    app.dependency_overrides[get_db] = lambda: (yield db)
    app.dependency_overrides[get_settings] = lambda: Settings(database_url="sqlite://", rpa_token="t")
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    app.dependency_overrides.clear()


def test_get_run(db):
    app.dependency_overrides[get_db] = lambda: (yield db)
    app.dependency_overrides[get_settings] = lambda: Settings(database_url="sqlite://", rpa_token="t")
    run = CatalogRun(id="r1", source_filename="p.csv", idempotency_key="k1")
    db.add(run)
    db.commit()
    client = TestClient(app)
    r = client.get("/v1/catalog/runs/r1")
    assert r.status_code == 200
    app.dependency_overrides.clear()


def test_missing_run_404(db):
    app.dependency_overrides[get_db] = lambda: (yield db)
    app.dependency_overrides[get_settings] = lambda: Settings(database_url="sqlite://", rpa_token="t")
    client = TestClient(app)
    r = client.get("/v1/catalog/runs/nonexistent")
    assert r.status_code == 404
    app.dependency_overrides.clear()