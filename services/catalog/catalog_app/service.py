import csv
import io
from collections import Counter
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import Settings
from .models import CatalogItem, CatalogRun, ERPOperation
from .schemas import ERPResultBatch
from .validation import validate_source

REQUIRED_COLUMNS = {"sku", "source_title", "category", "price", "stock", "attributes"}
PENDING_STATUSES = {"imported", "drafting"}


def import_catalog(
    db: Session,
    settings: Settings,
    content: bytes,
    filename: str,
    idempotency_key: str,
) -> tuple[CatalogRun, bool]:
    existing = db.scalar(select(CatalogRun).where(CatalogRun.idempotency_key == idempotency_key))
    if existing:
        return existing, True
    if len(content) > settings.max_csv_bytes:
        raise ValueError("csv_too_large")
    try:
        rows = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise ValueError("invalid_csv") from exc
    if not rows or len(rows) > settings.max_csv_rows:
        raise ValueError("csv_row_limit")
    if not REQUIRED_COLUMNS.issubset(rows[0]):
        raise ValueError("csv_missing_columns")

    run = CatalogRun(idempotency_key=idempotency_key, source_filename=filename)
    db.add(run)
    db.flush()
    normalized = [validate_source(row) for row in rows]
    duplicate_skus = {
        sku for sku, count in Counter(item[0]["sku"] for item in normalized).items() if count > 1
    }
    for values, errors in normalized:
        if values["sku"] in duplicate_skus:
            errors = [*errors, "duplicate_sku_in_run"]
        db.add(
            CatalogItem(
                run_id=run.id,
                **values,
                status="validation_failed" if errors else "imported",
                validation_errors=errors,
            )
        )
    if all(errors for _, errors in normalized):
        run.status = "completed"
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(CatalogRun).where(CatalogRun.idempotency_key == idempotency_key)
        )
        if existing:
            return existing, True
        raise
    db.refresh(run)
    return run, False


def run_payload(db: Session, run: CatalogRun) -> dict:
    counts = dict(
        db.execute(
            select(CatalogItem.status, func.count())
            .where(CatalogItem.run_id == run.id)
            .group_by(CatalogItem.status)
        ).all()
    )
    items = list(
        db.scalars(
            select(CatalogItem)
            .where(CatalogItem.run_id == run.id)
            .order_by(CatalogItem.sku)
        )
    )
    return {
        "run_id": run.id,
        "status": run.status,
        "source_filename": run.source_filename,
        "counts": counts,
        "items": [
            {
                "item_id": item.id,
                "sku": item.sku,
                "source_title": item.source_title,
                "category": item.category,
                "price": str(item.price),
                "stock": item.stock,
                "listing_title": item.listing_title,
                "selling_points": item.selling_points,
                "keywords": item.keywords,
                "status": item.status,
                "validation_errors": item.validation_errors,
                "draft_source": item.draft_source,
                "workflow_version": item.workflow_version,
                "erp_record_id": item.erp_record_id,
                "erp_error": item.erp_error,
            }
            for item in items
        ],
    }


def approved_csv(db: Session, run_id: str) -> str:
    items = list(
        db.scalars(
            select(CatalogItem)
            .where(
                CatalogItem.run_id == run_id,
                CatalogItem.status.in_({"ready_for_rpa", "erp_failed"}),
            )
            .order_by(CatalogItem.sku)
        )
    )
    output = io.StringIO()
    fieldnames = [
        "item_id",
        "sku",
        "listing_title",
        "category",
        "price",
        "stock",
        "selling_points",
        "keywords",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for item in items:
        writer.writerow(
            {
                "item_id": item.id,
                "sku": item.sku,
                "listing_title": item.listing_title,
                "category": item.category,
                "price": item.price,
                "stock": item.stock,
                "selling_points": " | ".join(item.selling_points),
                "keywords": " | ".join(item.keywords),
            }
        )
    return output.getvalue()


def record_erp_results(
    db: Session,
    run_id: str,
    batch: ERPResultBatch,
) -> list[dict]:
    responses: list[dict] = []
    for result in batch.results:
        existing = db.scalar(
            select(ERPOperation).where(ERPOperation.operation_key == result.operation_key)
        )
        if existing:
            responses.append({"operation_key": result.operation_key, "reused": True})
            continue
        item = db.scalar(
            select(CatalogItem).where(
                CatalogItem.id == result.item_id,
                CatalogItem.run_id == run_id,
            )
        )
        if item is None:
            raise ValueError("item_not_found")
        allowed = {"ready_for_rpa", "erp_failed"}
        if item.status not in allowed:
            raise ValueError("invalid_item_state")
        if result.result == "written" and not result.erp_record_id:
            raise ValueError("written_result_requires_erp_record_id")
        target_status = {
            "written": "erp_written",
            "failed": "erp_failed",
            "skipped": "erp_skipped",
        }[result.result]
        changed = db.execute(
            update(CatalogItem)
            .where(CatalogItem.id == item.id, CatalogItem.status.in_(allowed))
            .values(
                status=target_status,
                erp_record_id=result.erp_record_id,
                erp_error=result.error,
                updated_at=datetime.now(UTC),
            )
        )
        if changed.rowcount != 1:
            raise ValueError("concurrent_item_transition")
        db.add(
            ERPOperation(
                operation_key=result.operation_key,
                item_id=item.id,
                result=result.result,
                payload=result.model_dump(mode="json"),
            )
        )
        responses.append({"operation_key": result.operation_key, "reused": False})
    db.commit()
    refresh_run_status(db, run_id)
    return responses


def refresh_run_status(db: Session, run_id: str) -> None:
    run = db.get(CatalogRun, run_id)
    if run is None:
        return
    pending = db.scalar(
        select(func.count())
        .select_from(CatalogItem)
        .where(CatalogItem.run_id == run_id, CatalogItem.status.in_(PENDING_STATUSES))
    )
    if not pending:
        run.status = "completed"
        run.updated_at = datetime.now(UTC)
        db.commit()
