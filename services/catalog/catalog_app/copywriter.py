import json

import httpx

from .config import Settings
from .models import CatalogItem
from .schemas import CopyDraft


class CopywriterError(RuntimeError):  # NOSONAR - intentional marker: caught-and-re-raised before generic handlers
    pass


def generate_copy(settings: Settings, item: CatalogItem) -> tuple[CopyDraft, str]:
    if not settings.dify_catalog_workflow_api_key:
        if settings.allow_demo_copywriter:
            return deterministic_copy(item), "demo_rules"
        raise CopywriterError("catalog workflow key is not configured")
    try:
        response = httpx.post(
            f"{settings.dify_base_url.rstrip('/')}/workflows/run",
            headers={"Authorization": f"Bearer {settings.dify_catalog_workflow_api_key}"},
            json={
                "inputs": {
                    "sku": item.sku,
                    "source_title": item.source_title,
                    "category": item.category,
                    "attributes": item.attributes,
                },
                "response_mode": "blocking",
                "user": "catalog-ops-worker",
            },
            timeout=settings.dify_timeout_seconds,
        )
        response.raise_for_status()
        data = response.json().get("data", {})
        if data.get("status") != "succeeded":
            raise CopywriterError(data.get("error") or "workflow failed")
        raw = data.get("outputs", {}).get("catalog_copy_json")
        if isinstance(raw, str):
            raw = json.loads(raw)
        return CopyDraft.model_validate(raw), "dify"
    except CopywriterError:
        raise
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise CopywriterError("catalog copy request failed") from exc


def deterministic_copy(item: CatalogItem) -> CopyDraft:
    attributes = item.attributes or "基础款"
    listing_title = f"{item.source_title}｜{attributes}"
    if len(listing_title) < 10:
        listing_title = f"{listing_title}｜日常实用款"
    return CopyDraft(
        listing_title=listing_title[:60],
        selling_points=[
            f"适用于{item.category}日常使用",
            f"规格信息：{attributes}",
            "商品信息清晰，便于选购",
        ],
        keywords=[item.category, item.source_title[:10], "日常实用"],
    )
