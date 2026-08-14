import logging
import time

from sqlalchemy import select

from .config import get_settings
from .database import SessionLocal, init_db
from .models import AnalysisJob, utc_now
from .service import cleanup_expired_sessions, process_job, recover_stale_jobs

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("feedback-worker")


def run_once() -> bool:
    settings = get_settings()
    with SessionLocal() as db:
        job = db.scalar(
            select(AnalysisJob)
            .where(
                AnalysisJob.status == "queued",
                AnalysisJob.available_at <= utc_now(),
            )
            .order_by(AnalysisJob.created_at)
            .limit(1)
        )
        if job is None:
            return False
        try:
            process_job(db, settings, job)
        except Exception:
            logger.exception("analysis job %s failed", job.id)
        return True


def main() -> None:
    init_db()
    logger.info("feedback worker started")
    last_cleanup = 0.0
    while True:
        if time.monotonic() - last_cleanup >= 60:
            with SessionLocal() as db:
                removed = cleanup_expired_sessions(db)
                recovered = recover_stale_jobs(db)
            if removed:
                logger.info("removed %s expired demo sessions", removed)
            if recovered:
                logger.warning("requeued %s stale processing jobs", recovered)
            last_cleanup = time.monotonic()
        if not run_once():
            time.sleep(1)


if __name__ == "__main__":
    main()
