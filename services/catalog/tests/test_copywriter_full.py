from unittest.mock import patch, MagicMock
from catalog_app.copywriter import generate_copy, deterministic_copy
from catalog_app.config import Settings
from catalog_app.models import CatalogItem
from catalog_app.schemas import CopyDraft


def test_demo_when_no_key():
    s = Settings(dify_catalog_workflow_api_key="", allow_demo_copywriter=True)
    item = CatalogItem(sku="S1", source_title="T", category="c", attributes="a")
    _, src = generate_copy(s, item)
    assert src == "demo_rules"


def test_raises_when_no_key_no_demo():
    s = Settings(dify_catalog_workflow_api_key="", allow_demo_copywriter=False)
    item = CatalogItem(sku="S1", source_title="T", category="c", attributes="a")
    try:
        generate_copy(s, item)
    except RuntimeError:
        return
    raise AssertionError("should raise")


def test_dify_success():
    s = Settings(dify_catalog_workflow_api_key="k1", dify_base_url="http://api", dify_timeout_seconds=10)
    item = CatalogItem(sku="S1", source_title="T", category="c", attributes="a")
    mr = MagicMock()
    mr.raise_for_status.return_value = None
    mr.json.return_value = {"data": {"status": "succeeded", "outputs": {"catalog_copy_json": '{"listing_title":"Great","short_description":"n","selling_points":["a","b","c"],"target_audience":"all","keywords":["k1","k2","k3"]}'}}}
    with patch("httpx.post", return_value=mr):
        r, src = generate_copy(s, item)
    assert src == "dify"
    assert r.listing_title == "Great"


def test_dify_failed_status():
    s = Settings(dify_catalog_workflow_api_key="k1", dify_base_url="http://api", dify_timeout_seconds=10)
    item = CatalogItem(sku="S1", source_title="T", category="c", attributes="a")
    mr = MagicMock()
    mr.raise_for_status.return_value = None
    mr.json.return_value = {"data": {"status": "failed", "error": "workflow error"}}
    with patch("httpx.post", return_value=mr):
        try:
            generate_copy(s, item)
        except RuntimeError:
            pass
        else:
            raise AssertionError("should raise")


def test_deterministic():
    item = CatalogItem(sku="S1", source_title="Test Product Long", category="c", attributes="basic")
    r = deterministic_copy(item)
    assert isinstance(r, CopyDraft)
    assert len(r.listing_title) >= 10