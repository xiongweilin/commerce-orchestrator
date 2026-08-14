import argparse
import time
from collections import Counter
from pathlib import Path

import httpx

from catalog_app.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("data/sample-products.csv"))
    parser.add_argument("--out", type=Path, default=Path("artifacts/runtime/approved.csv"))
    parser.add_argument("--api", default="http://127.0.0.1:18200")
    parser.add_argument("--idempotency-key")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()

    key = args.idempotency_key or f"catalog-demo-{int(time.time())}"
    with args.source.open("rb") as handle:
        response = httpx.post(
            f"{args.api}/v1/catalog/imports",
            headers={"Idempotency-Key": key},
            files={"file": (args.source.name, handle, "text/csv")},
            timeout=30,
        )
    response.raise_for_status()
    run_id = response.json()["run_id"]
    deadline = time.monotonic() + args.timeout
    payload = None
    while time.monotonic() < deadline:
        payload = httpx.get(f"{args.api}/v1/catalog/runs/{run_id}", timeout=10).json()
        if payload["status"] == "completed":
            break
        time.sleep(1)
    if not payload or payload["status"] != "completed":
        raise TimeoutError(f"catalog run did not complete: {run_id}")

    settings = get_settings()
    approved = httpx.get(
        f"{args.api}/v1/catalog/runs/{run_id}/approved.csv",
        headers={"X-RPA-Token": settings.rpa_token},
        timeout=30,
    )
    approved.raise_for_status()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(approved.content)
    sources = Counter(item["draft_source"] or "none" for item in payload["items"])
    print(f"run_id={run_id}")
    print(f"counts={payload['counts']}")
    print(f"draft_sources={dict(sources)}")
    print(f"approved_csv={args.out}")


if __name__ == "__main__":
    main()
