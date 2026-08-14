import time
from datetime import UTC, datetime

from sqlalchemy import select

from .config import get_settings
from .copywriter import CopywriterError, generate_copy
from .database import SessionLocal, init_db
from .models import CatalogItem
from .service import refresh_run_status
from .validation import validate_draft


def process_one() -> bool:
    settings = get_settings()
    with SessionLocal() as db:
        item = db.scalar(
            select(CatalogItem)
            .where(CatalogItem.status == "imported")
            .order_by(CatalogItem.created_at)
            .with_for_update(skip_locked=True)
        )
        if item is None:
            return False
        item.status = "drafting"
        item.draft_attempts += 1
        item.updated_at = datetime.now(UTC)
        db.commit()
        try:
            draft, source = generate_copy(settings, item)
            errors = validate_draft(draft)
            item.listing_title = draft.listing_title
            item.selling_points = draft.selling_points
            item.keywords = draft.keywords
            item.draft_source = source
            item.workflow_version = settings.catalog_workflow_version if source == "dify" else None
            item.validation_errors = errors
            item.status = "validation_failed" if errors else "ready_for_rpa"
        except CopywriterError as exc:
            item.validation_errors = [f"copywriter_error:{exc}"]
            item.status = "imported" if item.draft_attempts < 3 else "draft_failed"
        item.updated_at = datetime.now(UTC)
        run_id = item.run_id
        db.commit()
        refresh_run_status(db, run_id)
        return True


def main() -> None:
    init_db()
    while True:
        if not process_one():
            time.sleep(1)


if __name__ == "__main__":
    main()
