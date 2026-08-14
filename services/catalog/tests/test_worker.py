from decimal import Decimal

import pytest

from catalog_app import worker
from catalog_app.config import Settings
from catalog_app.copywriter import CopywriterError
from catalog_app.models import CatalogItem, CatalogRun
from catalog_app.schemas import CopyDraft


class ExistingSessionContext:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self.db

    def __exit__(self, *_exc):
        return False


def add_item(db, *, attempts: int = 0) -> CatalogItem:
    run = CatalogRun(id="run-worker", source_filename="products.csv", idempotency_key="worker-key")
    item = CatalogItem(
        run=run,
        sku="BOX-001",
        source_title="Desktop storage box",
        category="office",
        price=Decimal("39.90"),
        stock=10,
        attributes="white",
        draft_attempts=attempts,
    )
    db.add(run)
    db.add(item)
    db.commit()
    return item


def valid_draft() -> CopyDraft:
    return CopyDraft(
        listing_title="Desktop storage box for office desks",
        selling_points=["keeps files sorted", "white finish", "daily office use"],
        keywords=["storage", "office", "desk"],
    )


def install_worker_db(monkeypatch: pytest.MonkeyPatch, db) -> None:
    monkeypatch.setattr(worker, "SessionLocal", lambda: ExistingSessionContext(db))
    monkeypatch.setattr(
        worker,
        "get_settings",
        lambda: Settings(database_url="sqlite://", catalog_workflow_version="workflow-v1"),
    )


def test_process_one_returns_false_when_no_imported_item(monkeypatch: pytest.MonkeyPatch, db) -> None:
    install_worker_db(monkeypatch, db)

    assert worker.process_one() is False


def test_process_one_moves_valid_draft_to_ready_for_rpa(
    monkeypatch: pytest.MonkeyPatch, db
) -> None:
    item = add_item(db)
    refreshed: list[str] = []
    install_worker_db(monkeypatch, db)
    monkeypatch.setattr(worker, "generate_copy", lambda _settings, _item: (valid_draft(), "dify"))
    monkeypatch.setattr(worker, "refresh_run_status", lambda _db, run_id: refreshed.append(run_id))

    assert worker.process_one() is True
    db.refresh(item)
    assert item.status == "ready_for_rpa"
    assert item.workflow_version == "workflow-v1"
    assert item.draft_source == "dify"
    assert refreshed == [item.run_id]


def test_process_one_keeps_validation_errors(monkeypatch: pytest.MonkeyPatch, db) -> None:
    item = add_item(db)
    install_worker_db(monkeypatch, db)
    monkeypatch.setattr(worker, "generate_copy", lambda _settings, _item: (valid_draft(), "demo_rules"))
    monkeypatch.setattr(worker, "validate_draft", lambda _draft: ["invalid_selling_points"])
    monkeypatch.setattr(worker, "refresh_run_status", lambda _db, _run_id: None)

    assert worker.process_one() is True
    db.refresh(item)
    assert item.status == "validation_failed"
    assert item.workflow_version is None
    assert item.validation_errors == ["invalid_selling_points"]


def test_process_one_retries_copywriter_errors(monkeypatch: pytest.MonkeyPatch, db) -> None:
    item = add_item(db)
    install_worker_db(monkeypatch, db)

    def fail(_settings, _item):
        raise CopywriterError("provider unavailable")

    monkeypatch.setattr(worker, "generate_copy", fail)
    monkeypatch.setattr(worker, "refresh_run_status", lambda _db, _run_id: None)

    assert worker.process_one() is True
    db.refresh(item)
    assert item.status == "imported"
    assert item.draft_attempts == 1
    assert item.validation_errors == ["copywriter_error:provider unavailable"]


def test_process_one_marks_third_copywriter_error_failed(
    monkeypatch: pytest.MonkeyPatch, db
) -> None:
    item = add_item(db, attempts=2)
    install_worker_db(monkeypatch, db)
    monkeypatch.setattr(
        worker, "generate_copy", lambda _settings, _item: (_ for _ in ()).throw(CopywriterError("bad"))
    )
    monkeypatch.setattr(worker, "refresh_run_status", lambda _db, _run_id: None)

    assert worker.process_one() is True
    db.refresh(item)
    assert item.status == "draft_failed"
    assert item.draft_attempts == 3


def test_main_sleeps_when_queue_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(worker, "init_db", lambda: calls.append("init"))
    monkeypatch.setattr(worker, "process_one", lambda: False)

    def stop_after_sleep(_seconds):
        calls.append("sleep")
        raise KeyboardInterrupt

    monkeypatch.setattr(worker.time, "sleep", stop_after_sleep)

    with pytest.raises(KeyboardInterrupt):
        worker.main()

    assert calls == ["init", "sleep"]
