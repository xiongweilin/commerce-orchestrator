from unittest.mock import patch, MagicMock
from shadowbot.catalog_odoo_rpa import main, _required, _operation_key


def test_required_ok():
    assert _required({"k": "v"}, "k") == "v"


def test_required_missing():
    try:
        _required({}, "k")
    except ValueError:
        pass
    else:
        raise AssertionError("should raise")


def test_operation_key_stable():
    a = _operation_key("r1", "i1", "written")
    b = _operation_key("r1", "i1", "failed", "timeout")
    assert a != b
    assert _operation_key("r1", "i1", "written") == a


def test_main_dry_run():
    mr = MagicMock()
    mr.__enter__.return_value = mr
    mr.read.return_value = b"item_id,approved\nsku-1,true\nsku-2,false\n"
    with patch("urllib.request.urlopen", return_value=mr):
        r = main({
            "api_base_url": "http://api",
            "run_id": "r1", "rpa_token": "t",
            "odoo_login_url": "http://odoo/login?db=db",
            "odoo_product_list_url": "http://odoo/list",
            "odoo_username": "u", "odoo_password": "p",
            "dry_run": "true",
        })
    assert r["dry_run"] is True
    assert r["approved"] == 2


def test_main_with_existing_product():
    mr = MagicMock()
    mr.__enter__.return_value = mr
    mr.read.side_effect = [
        b"item_id,approved\nsku-1,true\n",
        b'{"status":"ack"}',
    ]
    mp = MagicMock()
    mp.authenticate.return_value = 1
    mp.execute_kw.side_effect = [
        {"default_code": {"type": "char", "readonly": False},
         "name": {"type": "char", "readonly": False},
         "list_price": {"type": "float", "readonly": False},
         "detailed_type": {"type": "char", "readonly": False,
                           "selection": [["consu", "Goods"]]},
         "description_sale": {"type": "char", "readonly": False},
         "sale_ok": {"type": "boolean", "readonly": False}},
        [{"id": 99, "name": "T", "default_code": "sku-1"}],
    ]
    with patch("urllib.request.urlopen", return_value=mr), \
         patch("shadowbot.odoo_adapter.xmlrpc.client.ServerProxy", return_value=mp):
        r = main({
            "api_base_url": "http://api",
            "run_id": "r1", "rpa_token": "t",
            "odoo_login_url": "http://odoo/login?db=db",
            "odoo_product_list_url": "http://odoo/list",
            "odoo_username": "u", "odoo_password": "p",
        })
    assert r["approved"] == 1