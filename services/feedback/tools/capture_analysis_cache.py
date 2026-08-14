import argparse
import csv
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from feedback_app.analyzers import DifyAnalyzer
from feedback_app.config import get_settings
from feedback_app.schemas import TicketInput
from tools import safe_path


def load_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def row_hash(row: dict) -> str:
    canonical = json.dumps(
        {key: row[key] for key in ("ticket_id", "user_type", "channel", "message", "created_at")},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def load_cache(path: Path) -> dict:
    if not path.exists():
        return {"workflow_version": get_settings().workflow_version, "items": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("workflow_version") != get_settings().workflow_version:
        raise ValueError("analysis cache workflow version does not match current settings")
    return payload


def save_cache(path: Path, payload: dict) -> None:
    path = safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = safe_path(path.with_suffix(path.suffix + ".tmp"))
    temporary.write_text(  # NOSONAR -- canonical project-bound path from safe_path above
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def analyze_row(row: dict, max_attempts: int) -> dict:
    analyzer = DifyAnalyzer(get_settings())
    ticket = TicketInput(
        ticket_id=row["ticket_id"],
        user_type=row["user_type"],
        channel=row["channel"],
        message=row["message"],
        created_at=row["created_at"],
        current_status=row["current_status"],
    )
    error = "unknown"
    attempt_errors: list[str] = []
    for attempt in range(1, max_attempts + 1):
        try:
            result = analyzer.analyze(ticket)
            return {
                "input_sha256": row_hash(row),
                "attempts": attempt,
                "attempt_errors": attempt_errors,
                "analysis": result.model_dump(mode="json"),
            }
        except Exception as exc:  # the cache records dependency failures verbatim
            error = f"{type(exc).__name__}: {exc}"
            attempt_errors.append(error)
            if attempt < max_attempts:
                time.sleep(2 * attempt)
    return {
        "input_sha256": row_hash(row),
        "attempts": max_attempts,
        "attempt_errors": attempt_errors,
        "error": error,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--max-attempts", type=int, default=3)
    args = parser.parse_args()
    args.data = safe_path(args.data, must_exist=True)
    args.out = safe_path(args.out)
    if args.workers < 1 or args.max_attempts < 1:
        raise ValueError("workers and max-attempts must be positive")

    rows = load_rows(args.data)
    cache = load_cache(args.out)
    pending = [
        row
        for row in rows
        if cache["items"].get(row["ticket_id"], {}).get("input_sha256") != row_hash(row)
        or "analysis" not in cache["items"].get(row["ticket_id"], {})
    ]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(analyze_row, row, args.max_attempts): row["ticket_id"]
            for row in pending
        }
        for future in as_completed(futures):
            ticket_id = futures[future]
            cache["items"][ticket_id] = future.result()
            save_cache(args.out, cache)
            result = cache["items"][ticket_id]
            state = "ok" if "analysis" in result else "error"
            print(f"{ticket_id} {state} attempts={result['attempts']}", flush=True)

    succeeded = sum("analysis" in item for item in cache["items"].values())
    print(f"complete succeeded={succeeded}/{len(rows)} cache={args.out}")


if __name__ == "__main__":
    main()
