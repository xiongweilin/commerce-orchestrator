import argparse
import csv
import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, select

from feedback_app.config import get_settings
from feedback_app.database import SessionLocal, init_db
from feedback_app.embeddings import SentenceTransformerEmbedder, TfidfEmbedder
from feedback_app.models import Analysis, AnalysisJob, Ticket
from feedback_app.pipeline import rebuild_clusters, rebuild_weekly_report
from feedback_app.privacy import sanitize_message
from feedback_app.schemas import TicketInput
from feedback_app.service import enqueue_ticket, input_hash, process_job
from tools import safe_path


def load_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main()-> None:  # noqa: S3776 (tool script - acceptable complexity)
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--csv", type=Path, default=Path("data/generated/tickets_demo_runtime.csv")
    )
    parser.add_argument("--embedding", choices=["tfidf", "bge"], default="tfidf")
    parser.add_argument("--threshold", type=float)
    args = parser.parse_args()
    settings = get_settings()
    init_db()
    args.csv = safe_path(args.csv, must_exist=True)
    rows = load_rows(args.csv)
    with SessionLocal() as db:
        for row in rows:
            existing = db.scalar(select(Ticket).where(Ticket.external_id == row["ticket_id"]))
            payload = TicketInput(
                ticket_id=row["ticket_id"],
                user_type=row["user_type"],
                channel=row["channel"],
                message=row["message"],
                created_at=row["created_at"],
                current_status=row["current_status"],
            )
            if existing:
                sanitized = sanitize_message(payload.message)
                expected_hash = input_hash(sanitized, settings.workflow_version)
                if existing.input_hash == expected_hash:
                    continue
                db.execute(delete(Analysis).where(Analysis.ticket_id == existing.id))
                db.execute(delete(AnalysisJob).where(AnalysisJob.ticket_id == existing.id))
                existing.user_type = payload.user_type
                existing.channel = payload.channel
                existing.message = sanitized
                existing.created_at = payload.created_at
                existing.current_status = payload.current_status
                existing.input_hash = expected_hash
                # This command processes refreshes synchronously. Mark the job as processing
                # before commit so the long-running worker cannot race the same ticket.
                job = AnalysisJob(ticket_id=existing.id, status="processing")
                db.add(job)
                db.commit()
                process_job(db, settings, job)
                print(f"refreshed {existing.external_id}")
                continue
            ticket, job = enqueue_ticket(
                db,
                settings,
                payload,
                session_id=None,
                source="synthetic_seed",
            )
            process_job(db, settings, job)
            print(f"seeded {ticket.external_id}")

        if args.embedding == "bge":
            embedder = SentenceTransformerEmbedder(settings.embedding_model)
        else:
            embedder = TfidfEmbedder()
        threshold = args.threshold
        if threshold is None:
            evaluation = Path("artifacts/evaluation/evaluation.json")
            if evaluation.exists():
                payload = json.loads(evaluation.read_text(encoding="utf-8"))
                threshold = payload["clustering"]["frozen_threshold"]
            else:
                threshold = 0.80
        clusters = rebuild_clusters(db, embedder, threshold, settings=settings)
        as_of = max(datetime.fromisoformat(row["created_at"]) for row in rows)
        report = rebuild_weekly_report(db, as_of=as_of, settings=settings)
        print(
            f"complete tickets={len(rows)} clusters={len(clusters)} "
            f"week_start={report.week_start.isoformat()} threshold={threshold}"
        )


if __name__ == "__main__":
    main()
