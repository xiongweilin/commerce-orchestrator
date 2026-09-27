"""POST /v1/webhooks/shopify — verified, deduplicated Shopify webhook intake."""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import UnauthenticatedError, get_session
from app.config import get_settings
from app.core.security import verify_webhook_hmac
from app.services.webhooks import ingest_shopify_webhook

router = APIRouter(prefix="/v1", tags=["webhooks"])


class WebhookReceipt(BaseModel):
    """Fast acknowledgement returned to the webhook sender."""

    received: bool
    deduplicated: bool = False


@router.post("/webhooks/shopify", response_model=WebhookReceipt)
async def shopify_webhook(
    request: Request,
    db: Annotated[Session, Depends(get_session)],
) -> WebhookReceipt:
    """Ingest a Shopify webhook.

    HMAC-SHA256 verification runs over the raw request body before any work;
    missing or invalid signatures are rejected with 401 ``unauthenticated``.
    Deduplication, raw-body encryption and event emission are delegated to
    ``app.services.webhooks.ingest_shopify_webhook``. The endpoint responds
    200 immediately and never blocks on external calls.
    """
    raw_body = await request.body()
    signature = request.headers.get("x-shopify-hmac-sha256")
    secret = get_settings().shopify_webhook_secret
    if not signature or not secret or not verify_webhook_hmac(raw_body, signature, secret):
        raise UnauthenticatedError("Webhook HMAC verification failed")

    payload: dict[str, Any] = {}
    if raw_body:
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {}

    result = ingest_shopify_webhook(
        db,
        webhook_id=request.headers.get("x-shopify-webhook-id"),
        topic=request.headers.get("x-shopify-topic", ""),
        raw_body=raw_body,
        payload=payload,
        headers=dict(request.headers),
    )
    return WebhookReceipt.model_validate(result)
