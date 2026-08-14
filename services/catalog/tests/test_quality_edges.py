from decimal import Decimal
from types import SimpleNamespace
from urllib import error
import io
import xmlrpc.client

import httpx
import pytest
from fastapi.testclient import TestClient

from catalog_app.config import Settings, get_settings
from catalog_app.copywriter import CopywriterError, generate_copy
import catalog_app.database as database
from catalog_app.database import get_db
from catalog_app.main import app, lifespan
from catalog_app.models import CatalogItem, CatalogRun, ERPOperation
from catalog_app.schemas import CopyDraft, ERPResultBatch
from catalog_app.service import (
    import_catalog,
    record_erp_results,
    refresh_run_status,
)
from sqlalchemy.exc import IntegrityError
from catalog_app.validation import validate_draft
from shadowbot import catalog_odoo_rpa as rpa
from shadowbot import product_builder as builder
from shadowbot.odoo_adapter import OdooClient, _compact_fault, _parse_odoo_login_url


def csv_content(rows: list[str]) -> bytes:
    header = "sku,source_title,category,price,stock,attributes"
    return ("\n".join([header, *rows]) + "\n").encode()


def valid_row(sku: str = "BOX-001") -> str:
    return f"{sku},Desktop box,办公用品,39.90,20,white"


def add_run_and_item(db, status: str = "ready_for_rpa") -> tuple[CatalogRun, CatalogItem]:
    run = CatalogRun(idempotency_key=f"key-{status}", source_filename="products.csv")
    item = CatalogItem(
        run=run,
        sku=f"SKU-{status[:3].upper()}",
        source_title="Desktop box",
        category="办公用品",
        price=Decimal("39.90"),
        stock=20,
        attributes="white",
        listing_title="Desktop storage box",
        selling_points=["durable", "compact", "simple"],
        keywords=["box", "desk", "storage"],
        status=status,
    )
    db.add(run)
    db.add(item)
    db.commit()
    return run, item


def batch(item_id: str, operation_key: str, result: str, **kwargs) -> ERPResultBatch:
    return ERPResultBatch.model_validate(
        {"results": [{"item_id": item_id, "operation_key": operation_key, "result": result, **kwargs}]}
    )


def make_client(db, **settings_kwargs) -> TestClient:
    app.dependency_overrides.clear()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: Settings(
        database_url="sqlite://",
        rpa_token="secret",
        **settings_kwargs,
    )
    return TestClient(app)


def test_database_session_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    class FakeSession:
        def __enter__(self):
            events.append("enter")
            return self

        def __exit__(self, *_args):
            events.append("exit")
            return False

    fake_session = FakeSession()
    monkeypatch.setattr(database, "SessionLocal", lambda: fake_session)

    generator = database.get_db()
    assert next(generator) is fake_session
    with pytest.raises(StopIteration):
        next(generator)
    assert events == ["enter", "exit"]

    create_all_calls: list[object] = []
    monkeypatch.setattr(
        database.Base.metadata,
        "create_all",
        lambda engine: create_all_calls.append(engine),
    )
    database.init_db()
    assert create_all_calls == [database.engine]
    assert database.session_scope() is fake_session


def test_import_catalog_validates_csv_boundaries_and_all_invalid_run(db) -> None:
    settings = Settings(database_url="sqlite://", max_csv_bytes=32, max_csv_rows=1)

    with pytest.raises(ValueError, match="csv_too_large"):
        import_catalog(db, settings, b"x" * 33, "products.csv", "too-large")
    with pytest.raises(ValueError, match="invalid_csv"):
        import_catalog(db, settings, b"\xff\xfe\xfd", "products.csv", "invalid")
    with pytest.raises(ValueError, match="csv_row_limit"):
        import_catalog(db, settings, b"", "products.csv", "empty")
    with pytest.raises(ValueError, match="csv_missing_columns"):
        import_catalog(db, settings, b"sku,price\nBOX-1,10\n", "products.csv", "columns")

    invalid_run, reused = import_catalog(
        db,
        Settings(database_url="sqlite://"),
        csv_content(["bad,No category,Unknown,0,1000000,"]),
        "products.csv",
        "all-invalid",
    )

    assert reused is False
    assert invalid_run.status == "completed"
    assert db.query(CatalogItem).filter_by(run_id=invalid_run.id).one().validation_errors


class RaceDB:
    def __init__(self, existing_after_rollback: bool) -> None:
        self.existing_after_rollback = existing_after_rollback
        self.scalar_calls = 0
        self.rolled_back = False

    def scalar(self, _statement):
        self.scalar_calls += 1
        if self.scalar_calls == 1 or not self.existing_after_rollback:
            return None
        return CatalogRun(id="existing", idempotency_key="race", source_filename="old.csv")

    def add(self, _item) -> None:
        return None

    def flush(self) -> None:
        return None

    def commit(self) -> None:
        raise IntegrityError("insert", {}, RuntimeError("duplicate key"))

    def rollback(self) -> None:
        self.rolled_back = True

    def refresh(self, _item) -> None:
        return None


def test_import_catalog_handles_idempotency_race_after_integrity_error() -> None:
    fake_db = RaceDB(existing_after_rollback=True)

    run, reused = import_catalog(
        fake_db,
        Settings(database_url="sqlite://"),
        csv_content([valid_row()]),
        "products.csv",
        "race",
    )

    assert run.id == "existing"
    assert reused is True
    assert fake_db.rolled_back is True


def test_import_catalog_reraises_integrity_error_when_no_existing_run() -> None:
    fake_db = RaceDB(existing_after_rollback=False)

    with pytest.raises(IntegrityError):
        import_catalog(
            fake_db,
            Settings(database_url="sqlite://"),
            csv_content([valid_row()]),
            "products.csv",
            "race",
        )


def test_record_erp_results_validates_state_and_terminal_transitions(db) -> None:
    run, item = add_run_and_item(db)

    with pytest.raises(ValueError, match="item_not_found"):
        record_erp_results(db, run.id, batch("missing", "op-missing-1", "failed"))

    item.status = "imported"
    db.commit()
    with pytest.raises(ValueError, match="invalid_item_state"):
        record_erp_results(db, run.id, batch(item.id, "op-invalid-1", "failed"))

    item.status = "ready_for_rpa"
    db.commit()
    with pytest.raises(ValueError, match="written_result_requires_erp_record_id"):
        record_erp_results(db, run.id, batch(item.id, "op-written-1", "written"))

    failed = record_erp_results(
        db,
        run.id,
        batch(item.id, "op-failed-1", "failed", error="Odoo rejected data"),
    )
    db.refresh(item)
    assert failed == [{"operation_key": "op-failed-1", "reused": False}]
    assert item.status == "erp_failed"

    skipped = record_erp_results(db, run.id, batch(item.id, "op-skipped-1", "skipped"))
    db.refresh(item)
    db.refresh(run)
    assert skipped[0]["reused"] is False
    assert item.status == "erp_skipped"
    assert run.status == "completed"
    assert db.query(ERPOperation).count() == 2


def test_record_erp_results_detects_concurrent_transition(
    db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, item = add_run_and_item(db)
    monkeypatch.setattr(db, "execute", lambda *_args, **_kwargs: SimpleNamespace(rowcount=0))

    with pytest.raises(ValueError, match="concurrent_item_transition"):
        record_erp_results(
            db,
            run.id,
            batch(item.id, "op-concurrent-1", "written", erp_record_id="odoo-1"),
        )


def test_refresh_run_status_leaves_missing_or_pending_runs_unchanged(db) -> None:
    refresh_run_status(db, "missing")
    run, _item = add_run_and_item(db, status="imported")

    refresh_run_status(db, run.id)

    db.refresh(run)
    assert run.status == "processing"


@pytest.mark.anyio
async def test_api_lifespan_and_catalog_endpoints(db, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr("catalog_app.main.init_db", lambda: calls.append("init"))

    async with lifespan(app):
        calls.append("inside")
    assert calls == ["init", "inside"]

    client = make_client(db, max_csv_bytes=1024)
    bad_key = client.post(
        "/v1/catalog/imports",
        files={"file": ("products.csv", csv_content([valid_row()]), "text/csv")},
    )
    assert bad_key.status_code == 400

    invalid_csv = client.post(
        "/v1/catalog/imports",
        headers={"Idempotency-Key": "import-api-invalid"},
        files={"file": ("products.csv", b"\xff\xfe\xfd", "text/csv")},
    )
    assert invalid_csv.status_code == 400
    assert invalid_csv.json()["detail"] == "invalid_csv"

    created = client.post(
        "/v1/catalog/imports",
        headers={"Idempotency-Key": "import-api-1"},
        files={"file": ("products.csv", csv_content([valid_row()]), "text/csv")},
    )
    assert created.status_code == 202
    assert created.json()["reused"] is False

    reused = client.post(
        "/v1/catalog/imports",
        headers={"Idempotency-Key": "import-api-1"},
        files={"file": ("products.csv", csv_content([valid_row()]), "text/csv")},
    )
    assert reused.json()["reused"] is True

    run_id = created.json()["run_id"]
    assert client.get(f"/v1/catalog/runs/{run_id}/approved.csv").status_code == 401
    assert client.get(
        "/v1/catalog/runs/missing/approved.csv",
        headers={"X-RPA-Token": "secret"},
    ).status_code == 404

    assert client.post(
        f"/v1/catalog/runs/{run_id}/erp-results",
        json={"results": [{"item_id": "missing", "operation_key": "op-api-1", "result": "failed"}]},
    ).status_code == 401
    assert client.post(
        "/v1/catalog/runs/missing/erp-results",
        headers={"X-RPA-Token": "secret"},
        json={"results": [{"item_id": "missing", "operation_key": "op-api-2", "result": "failed"}]},
    ).status_code == 404
    assert client.post(
        f"/v1/catalog/runs/{run_id}/erp-results",
        headers={"X-RPA-Token": "secret"},
        json={"results": [{"item_id": "missing", "operation_key": "op-api-3", "result": "failed"}]},
    ).status_code == 409

    item = db.query(CatalogItem).filter_by(run_id=run_id).one()
    item.status = "ready_for_rpa"
    db.commit()
    success = client.post(
        f"/v1/catalog/runs/{run_id}/erp-results",
        headers={"X-RPA-Token": "secret"},
        json={
            "results": [
                {
                    "item_id": item.id,
                    "operation_key": "op-api-4",
                    "result": "written",
                    "erp_record_id": "odoo-product-1",
                }
            ]
        },
    )
    assert success.status_code == 200
    assert success.json()["results"] == [{"operation_key": "op-api-4", "reused": False}]

    app.dependency_overrides.clear()


def test_rpa_request_json_retries_http_and_url_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[int] = []
    monkeypatch.setattr(rpa, "sleep", lambda seconds: sleeps.append(seconds))

    def http_fail(*_args, **_kwargs):
        raise error.HTTPError(
            "http://api",
            409,
            "Conflict",
            hdrs=None,
            fp=io.BytesIO(b'{"detail":"bad state"}'),
        )

    monkeypatch.setattr(rpa.request, "urlopen", http_fail)
    with pytest.raises(RuntimeError, match="HTTPError 409"):
        rpa._request_json("http://api", "token", {"ok": False})
    assert sleeps == [1, 2]

    monkeypatch.setattr(
        rpa.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error.URLError("offline")),
    )
    with pytest.raises(RuntimeError, match="offline"):
        rpa._request_json("http://api", "token", {"ok": False})

    class UnreadableHTTPError(error.HTTPError):
        def read(self):
            raise RuntimeError("cannot read body")

    monkeypatch.setattr(
        rpa.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            UnreadableHTTPError("http://api", 500, "Server Error", hdrs=None, fp=None)
        ),
    )
    with pytest.raises(RuntimeError, match="HTTPError 500"):
        rpa._request_json("http://api", "token", {"ok": False})


def test_rpa_main_records_missing_sku_failure_and_run_wrapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    callbacks: list[tuple[str, str | None]] = []

    monkeypatch.setattr(
        rpa,
        "_approved_items",
        lambda *_args: [{"item_id": "item-1", "sku": "", "listing_title": "Missing SKU"}],
    )

    class FakeOdoo:
        db = "db"
        uid = 1

        def __init__(self, *_args):
            return None

    monkeypatch.setattr(rpa, "OdooClient", FakeOdoo)
    monkeypatch.setattr(
        rpa,
        "_callback",
        lambda _base, _run, _token, _item, result, record_id=None, message=None: callbacks.append(
            (result, message)
        ),
    )

    result = rpa.main(
        {
            "api_base_url": "http://api",
            "run_id": "run-1",
            "rpa_token": "token",
            "odoo_login_url": "http://odoo/login?db=db",
            "odoo_product_list_url": "http://odoo/list",
            "odoo_username": "user",
            "odoo_password": "password",
        }
    )

    assert result["failed"] == 1
    assert callbacks[0][0] == "failed"
    assert "missing sku" in callbacks[0][1]

    monkeypatch.setattr(rpa, "main", lambda args: args)
    wrapped = rpa.run("a", "r", "t", "login", "list", "u", "p", 10, True)
    assert wrapped["run_id"] == "r"
    assert wrapped["dry_run"] is True
    assert rpa.sleep(1) is None


def test_rpa_main_records_written_item(monkeypatch: pytest.MonkeyPatch) -> None:
    callbacks: list[tuple[str, str | None]] = []
    item = {"item_id": "item-1", "sku": "SKU-001", "listing_title": "Ready"}
    monkeypatch.setattr(rpa, "_approved_items", lambda *_args: [item])

    class FakeOdoo:
        db = "db"
        uid = 1

        def __init__(self, *_args):
            return None

    monkeypatch.setattr(rpa, "OdooClient", FakeOdoo)
    monkeypatch.setattr(rpa, "create_product", lambda _odoo, _item: "odoo-product-101")
    monkeypatch.setattr(
        rpa,
        "_callback",
        lambda _base, _run, _token, _item, result, record_id=None, message=None: callbacks.append(
            (result, record_id)
        ),
    )

    result = rpa.main(
        {
            "api_base_url": "http://api",
            "run_id": "run-1",
            "rpa_token": "token",
            "odoo_login_url": "http://odoo/login?db=db",
            "odoo_product_list_url": "http://odoo/list",
            "odoo_username": "user",
            "odoo_password": "password",
        }
    )

    assert result["written"] == 1
    assert callbacks == [("written", "odoo-product-101")]


def test_copywriter_wraps_transport_and_validation_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    item = CatalogItem(sku="S1", source_title="Title", category="办公用品", attributes="white")
    settings = Settings(dify_catalog_workflow_api_key="key", dify_base_url="http://dify")

    monkeypatch.setattr(
        "catalog_app.copywriter.httpx.post",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(httpx.HTTPError("timeout")),
    )
    with pytest.raises(CopywriterError, match="request failed"):
        generate_copy(settings, item)


def test_validation_rejects_bad_copy_lengths_and_keywords() -> None:
    draft = CopyDraft(
        listing_title="short",
        selling_points=["dup", "dup", "x"],
        keywords=["same", "same", "keyword-with-more-than-twenty-chars"],
    )

    assert validate_draft(draft) == [
        "listing_title_length",
        "invalid_selling_points",
        "invalid_keywords",
    ]


def test_odoo_adapter_error_and_lookup_edges() -> None:
    client = OdooClient.__new__(OdooClient)
    client.db = "db"
    client.uid = 1
    client.password = "password"

    class FaultyModels:
        def execute_kw(self, *_args, **_kwargs):
            raise xmlrpc.client.Fault(1, "Traceback\nValueError: bad value")

    client._models = FaultyModels()
    with pytest.raises(RuntimeError, match="ValueError: bad value"):
        client.execute("product.template", "create", [])

    client._fields_cache = {"product.template": {"name": {"readonly": True}}}
    assert client.field_writable("product.template", "missing") is False
    assert client.field_writable("product.template", "name") is False
    assert OdooClient.selection_keys({"selection": ["free", ["paid", "Paid"]]}) == [
        "free",
        "paid",
    ]

    assert _parse_odoo_login_url("http://odoo/web/login?db=erp") == ("http://odoo", "erp")
    with pytest.raises(ValueError, match="invalid odoo_login_url"):
        _parse_odoo_login_url("not-a-url")
    assert _compact_fault(Exception("")) == ""


def test_odoo_adapter_product_lookup_edges() -> None:
    client = OdooClient.__new__(OdooClient)
    client._fields_cache = {"product.template": {}, "product.product": {}}
    client.fields_get = lambda model: client._fields_cache[model]
    client.execute = lambda *_args, **_kwargs: []

    assert client.search_template_by_sku("SKU") is None
    assert client.search_variant_template_id("SKU") is None

    def fail_fields(_model):
        raise RuntimeError("fields unavailable")

    client.fields_get = fail_fields
    assert client.search_variant_template_id("SKU") is None

    client.fields_get = lambda model: {"default_code": {}}
    client.execute = lambda *_args, **_kwargs: []
    assert client.search_variant_template_id("SKU") is None

    client.execute = lambda *_args, **_kwargs: [{"product_tmpl_id": "bad"}]
    assert client.search_variant_template_id("SKU") is None

    client.search_template_by_sku = lambda _sku: None
    client.search_variant_template_id = lambda _sku: 77
    assert client.product_exists("SKU") == 77


def test_odoo_authentication_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeCommon:
        def authenticate(self, *_args, **_kwargs):
            return False

    monkeypatch.setattr("shadowbot.odoo_adapter.xmlrpc.client.ServerProxy", lambda *_args, **_kw: FakeCommon())

    with pytest.raises(RuntimeError, match="odoo authentication failed"):
        OdooClient._authenticate("http://odoo", "db", "user", "password")


class FakeBuilderOdoo:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.created_ids = [101]
        self.existing_id = None
        self.fields = {
            "product.template": {
                "name": {"readonly": False},
                "default_code": {"readonly": False},
                "list_price": {"readonly": False},
                "description_sale": {"readonly": False},
                "description": {"readonly": False},
                "sale_ok": {"readonly": False},
                "purchase_ok": {"readonly": False},
                "type": {"readonly": False, "selection": [["product", "Product"]]},
            },
            "product.product": {"default_code": {"readonly": False}},
        }

    def fields_get(self, model: str) -> dict:
        return self.fields[model]

    def field_writable(self, model: str, name: str) -> bool:
        return name in self.fields.get(model, {}) and not self.fields[model][name].get(
            "readonly", False
        )

    @staticmethod
    def selection_keys(field_def: dict) -> list[str]:
        return OdooClient.selection_keys(field_def)

    def execute(self, model: str, method: str, args=None, kwargs=None):
        self.calls.append((model, method, args, kwargs))
        if method == "create":
            return self.created_ids.pop(0)
        if method == "search_read":
            return []
        return True

    def product_exists(self, _sku: str):
        return self.existing_id


def builder_item(**overrides) -> dict:
    item = {
        "item_id": "item-1",
        "sku": "SKU-001",
        "listing_title": "Desktop storage box",
        "price": "¥39.90",
        "category": "办公用品",
        "stock": "20",
        "selling_points": "durable",
        "keywords": "box",
    }
    item.update(overrides)
    return item


def test_product_builder_value_and_variant_edges(capsys: pytest.CaptureFixture[str]) -> None:
    assert builder.parse_price("-") == 0.0

    odoo = FakeBuilderOdoo()
    assert builder.pick_product_type_value(odoo) == ("type", "product")

    detailed_product = FakeBuilderOdoo()
    detailed_product.fields["product.template"] = {
        "detailed_type": {"readonly": False, "selection": [["product", "Product"]]}
    }
    assert builder.pick_product_type_value(detailed_product) == ("detailed_type", "product")

    type_consu = FakeBuilderOdoo()
    type_consu.fields["product.template"] = {
        "type": {"readonly": False, "selection": [["consu", "Consumable"]]}
    }
    assert builder.pick_product_type_value(type_consu) == ("type", "consu")

    unknown_types = FakeBuilderOdoo()
    unknown_types.fields["product.template"] = {
        "detailed_type": {"readonly": False, "selection": [["service", "Service"]]},
        "type": {"readonly": False, "selection": [["service", "Service"]]},
    }
    assert builder.pick_product_type_value(unknown_types) == (None, None)
    assert "selections" in capsys.readouterr().out

    with pytest.raises(ValueError, match="missing sku"):
        builder.build_product_values(odoo, builder_item(sku=""), "full")
    with pytest.raises(ValueError, match="missing listing_title"):
        builder.build_product_values(odoo, builder_item(listing_title=""), "full")

    class NoVariantOdoo(FakeBuilderOdoo):
        def fields_get(self, model: str) -> dict:
            if model == "product.product":
                raise RuntimeError("no variants")
            return super().fields_get(model)

    assert builder.write_variant_sku(NoVariantOdoo(), 1, "SKU") is False
    assert "cannot read product.product fields" in capsys.readouterr().out

    assert builder.write_variant_sku(FakeBuilderOdoo(), 1, "SKU") is False


def test_product_builder_create_fallbacks_and_create_product(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FallbackOdoo(FakeBuilderOdoo):
        def __init__(self) -> None:
            super().__init__()
            self.failures = 1

        def execute(self, model: str, method: str, args=None, kwargs=None):
            if method == "create" and self.failures:
                self.failures -= 1
                raise RuntimeError("full mode rejected")
            return super().execute(model, method, args, kwargs)

    fallback = FallbackOdoo()
    assert builder.create_template_with_fallback(fallback, builder_item()) == 101
    assert any(call[1] == "write" for call in fallback.calls)

    class AlwaysFailOdoo(FakeBuilderOdoo):
        def execute(self, model: str, method: str, args=None, kwargs=None):
            if method == "create":
                raise RuntimeError("no create")
            return super().execute(model, method, args, kwargs)

    with pytest.raises(RuntimeError, match="all product.template create attempts failed"):
        builder.create_template_with_fallback(AlwaysFailOdoo(), builder_item())
    assert "create attempt" in capsys.readouterr().out

    existing = FakeBuilderOdoo()
    existing.existing_id = 42
    assert builder.create_product(existing, builder_item()) == "odoo-existing-42"
    assert builder.create_product(FakeBuilderOdoo(), builder_item()) == "odoo-product-101"


def test_optional_update_catches_template_and_variant_write_errors(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FailingWritesOdoo(FakeBuilderOdoo):
        def execute(self, model: str, method: str, args=None, kwargs=None):
            if method == "search_read":
                return [{"id": 99, "default_code": "OLD"}]
            if method == "write":
                raise RuntimeError("write failed")
            return super().execute(model, method, args, kwargs)

    builder.after_create_update_optional_fields(FailingWritesOdoo(), 101, builder_item())

    output = capsys.readouterr().out
    assert "optional template write failed" in output
    assert "variant sku write failed" in output
