from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .database import get_db, init_db
from .models import CatalogRun
from .schemas import ERPResultBatch
from .service import approved_csv, import_catalog, record_erp_results, run_payload


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Catalog Operations Automation API", version="0.1.0", lifespan=lifespan)


def require_rpa_token(settings: Settings, token: str | None) -> None:
    if not token or token != settings.rpa_token:
        raise HTTPException(status_code=401, detail="invalid_rpa_token")


@app.get("/health")
def health(db: Annotated[Session, Depends(get_db)]) -> dict:
    db.execute(text("SELECT 1"))
    return {"status": "ok", "service": "catalog-ops-api"}


@app.post(
    "/v1/catalog/imports",
    status_code=202,
    responses={
        400: {"description": "Invalid idempotency key, CSV too large, or malformed CSV"},
    },
)
async def create_import(
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    file: Annotated[UploadFile, File()],
    idempotency_key: Annotated[str | None, Header()] = None,
) -> dict:
    if not idempotency_key or len(idempotency_key) > 128:
        raise HTTPException(status_code=400, detail="invalid_idempotency_key")
    content = await file.read(settings.max_csv_bytes + 1)
    try:
        run, reused = import_catalog(
            db,
            settings,
            content,
            file.filename or "catalog.csv",
            idempotency_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"run_id": run.id, "status": run.status, "reused": reused}


@app.get(
    "/v1/catalog/runs/{run_id}",
    responses={404: {"description": "Catalog run not found"}},
)
def get_run(run_id: str, db: Annotated[Session, Depends(get_db)]) -> dict:
    run = db.get(CatalogRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run_not_found")
    return run_payload(db, run)


@app.get(
    "/v1/catalog/runs/{run_id}/approved.csv",
    responses={
        401: {"description": "Invalid or missing RPA token"},
        404: {"description": "Catalog run not found"},
    },
)
def get_approved_csv(
    run_id: str,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    x_rpa_token: Annotated[str | None, Header()] = None,
) -> Response:
    require_rpa_token(settings, x_rpa_token)
    if db.get(CatalogRun, run_id) is None:
        raise HTTPException(status_code=404, detail="run_not_found")
    return Response(
        approved_csv(db, run_id),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="approved-{run_id}.csv"'},
    )


@app.post(
    "/v1/catalog/runs/{run_id}/erp-results",
    responses={
        401: {"description": "Invalid or missing RPA token"},
        404: {"description": "Catalog run not found"},
        409: {"description": "Invalid item state or concurrent transition"},
    },
)
def post_erp_results(
    run_id: str,
    batch: ERPResultBatch,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    x_rpa_token: Annotated[str | None, Header()] = None,
) -> dict:
    require_rpa_token(settings, x_rpa_token)
    if db.scalar(select(CatalogRun.id).where(CatalogRun.id == run_id)) is None:
        raise HTTPException(status_code=404, detail="run_not_found")
    try:
        results = record_erp_results(db, run_id, batch)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"results": results}
