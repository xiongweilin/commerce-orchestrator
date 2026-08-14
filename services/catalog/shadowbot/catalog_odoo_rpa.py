# -*- coding: utf-8 -*-
"""Catalog RPA orchestrator: fetch approved items, push them into Odoo.

This module owns HTTP callbacks, CSV ingestion, and the main processing
loop.  Odoo XML-RPC access lives in `odoo_adapter.OdooClient`; product-
creation logic lives in `product_builder`.
"""

import builtins
import csv
import hashlib
import io
import json
from urllib import error, request

try:
    import xbot
    from xbot import sleep
    from xbot import print as xbot_print
    print = xbot_print  # noqa: A001
except ImportError:
    xbot = None
    print = builtins.print  # noqa: A001

    def sleep(_seconds):  # noqa: D103
        return None

from shadowbot.odoo_adapter import OdooClient
from shadowbot.product_builder import create_product

# -----------------------------------------------------------------------
# argument helpers
# -----------------------------------------------------------------------

def _required(args, name):
    value = str(args.get(name, "")).strip()
    if not value:
        raise ValueError("missing argument: {}".format(name))
    return value


def _item_value(item, name, default=""):
    return str(item.get(name, default) or "").strip()

# -----------------------------------------------------------------------
# HTTP helpers
# -----------------------------------------------------------------------

def _request_bytes(url, rpa_token, timeout=30):
    req = request.Request(url, headers={"X-RPA-Token": rpa_token})
    with request.urlopen(req, timeout=timeout) as response:
        return response.read()


def _request_json(url, rpa_token, payload, timeout=30):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last_error = None
    for attempt in range(1, 4):
        req = request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "X-RPA-Token": rpa_token,
            },
        )
        try:
            with request.urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace")
            except Exception:
                detail = ""
            last_error = "HTTPError {} {}".format(exc.code, detail[:500])
            if attempt < 3:
                sleep(attempt)
        except error.URLError as exc:
            last_error = exc
            if attempt < 3:
                sleep(attempt)
    raise RuntimeError("callback failed after retries: {}".format(last_error))


def _approved_items(api_base_url, run_id, rpa_token):
    url = "{}/v1/catalog/runs/{}/approved.csv".format(
        api_base_url.rstrip("/"),
        run_id,
    )
    content = _request_bytes(url, rpa_token).decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(content)))

# -----------------------------------------------------------------------
# callback helpers
# -----------------------------------------------------------------------

def _operation_key(run_id, item_id, result, detail=""):
    if result == "written":
        suffix = "written-v1"
    else:
        digest = hashlib.sha1(detail.encode("utf-8")).hexdigest()[:12]
        suffix = "failed-{}".format(digest)
    return "catalog-rpa:{}:{}:{}".format(run_id, item_id, suffix)


def _callback(api_base_url, run_id, rpa_token, item, result, record_id=None, message=None):
    item_id = _item_value(item, "item_id")
    detail = (message or "")[:500]
    payload = {
        "results": [
            {
                "item_id": item_id,
                "operation_key": _operation_key(run_id, item_id, result, detail),
                "result": result,
                "erp_record_id": record_id,
                "error": detail or None,
            }
        ]
    }
    url = "{}/v1/catalog/runs/{}/erp-results".format(
        api_base_url.rstrip("/"),
        run_id,
    )
    return _request_json(url, rpa_token, payload)

# -----------------------------------------------------------------------
# main orchestrator
# -----------------------------------------------------------------------

def main(args):
    api_base_url = _required(args, "api_base_url")
    run_id = _required(args, "run_id")
    rpa_token = _required(args, "rpa_token")
    odoo_login_url = _required(args, "odoo_login_url")
    _required(args, "odoo_product_list_url")
    odoo_username = _required(args, "odoo_username")
    odoo_password = _required(args, "odoo_password")

    max_items = int(args.get("max_items", 100))
    dry_run = str(args.get("dry_run", "false")).strip().lower() in (
        "1",
        "true",
        "yes",
        "y",
    )

    items = _approved_items(api_base_url, run_id, rpa_token)[:max_items]
    print("approved items: {}".format(len(items)))

    if dry_run:
        return {
            "approved": len(items),
            "written": 0,
            "failed": 0,
            "dry_run": True,
        }

    odoo = OdooClient(odoo_login_url, odoo_username, odoo_password)
    print("odoo connected: db={}, uid={}".format(odoo.db, odoo.uid))

    written = 0
    failed = 0

    for item in items:
        sku = _item_value(item, "sku")
        try:
            if not sku:
                raise ValueError(
                    "missing sku for item_id={}".format(_item_value(item, "item_id"))
                )
            print("processing: {}".format(sku))

            record_id = create_product(odoo, item)

            _callback(
                api_base_url, run_id, rpa_token, item,
                "written", record_id=record_id,
            )
            written += 1
            print("written: {} {}".format(sku, record_id))

        except Exception as exc:
            message = "{}: {}".format(type(exc).__name__, exc)[:500]
            _callback(
                api_base_url, run_id, rpa_token, item,
                "failed", message=message,
            )
            failed += 1
            print("failed: {} {}".format(sku, message))

    return {
        "approved": len(items),
        "written": written,
        "failed": failed,
        "dry_run": False,
    }


def run(
    api_base_url,
    run_id,
    rpa_token,
    odoo_login_url,
    odoo_product_list_url,
    odoo_username,
    odoo_password,
    max_items,
    dry_run,
):
    return main({
        "api_base_url": api_base_url,
        "run_id": run_id,
        "rpa_token": rpa_token,
        "odoo_login_url": odoo_login_url,
        "odoo_product_list_url": odoo_product_list_url,
        "odoo_username": odoo_username,
        "odoo_password": odoo_password,
        "max_items": max_items,
        "dry_run": dry_run,
    })
