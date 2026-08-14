from unittest.mock import patch, MagicMock
from shadowbot.odoo_adapter import OdooClient, _parse_odoo_login_url, _compact_fault

MP = MagicMock()

class TestOdooClient:
    def test_init_authenticates(self):
        p = MagicMock()
        p.authenticate.return_value = 42
        with patch("shadowbot.odoo_adapter.xmlrpc.client.ServerProxy", return_value=p):
            c = OdooClient("http://odoo/login?db=mydb", "u", "p")
        assert c.uid == 42
        assert c.db == "mydb"

    def test_execute(self):
        p = MagicMock()
        p.authenticate.return_value = 1
        p.execute_kw.return_value = [99]
        with patch("shadowbot.odoo_adapter.xmlrpc.client.ServerProxy", return_value=p):
            c = OdooClient("http://odoo/login?db=db", "u", "p")
            r = c.execute("product.template", "search", [[["default_code","=","SKU"]]])
        assert r == [99]

    def test_fields_get(self):
        p = MagicMock()
        p.authenticate.return_value = 1
        p.execute_kw.return_value = {"name": {"type": "char"}}
        with patch("shadowbot.odoo_adapter.xmlrpc.client.ServerProxy", return_value=p):
            c = OdooClient("http://odoo/login?db=db", "u", "p")
            f = c.fields_get("product.template")
        assert f["name"]["type"] == "char"

    def test_field_exists(self):
        p = MagicMock()
        p.authenticate.return_value = 1
        p.execute_kw.return_value = {"name": {"type": "char"}}
        with patch("shadowbot.odoo_adapter.xmlrpc.client.ServerProxy", return_value=p):
            c = OdooClient("http://odoo/login?db=db", "u", "p")
            assert c.field_exists("product.template", "name")
            assert not c.field_exists("product.template", "missing")

    def test_search_template_by_sku(self):
        p = MagicMock()
        p.authenticate.return_value = 1
        p.execute_kw.side_effect = [
            {"default_code": {"type": "char"}},
            [{"id": 55, "name": "T", "default_code": "SKU"}],
        ]
        with patch("shadowbot.odoo_adapter.xmlrpc.client.ServerProxy", return_value=p):
            c = OdooClient("http://odoo/login?db=db", "u", "p")
            r = c.search_template_by_sku("SKU")
        assert r == 55

    def test_search_variant_template_id(self):
        p = MagicMock()
        p.authenticate.return_value = 1
        p.execute_kw.side_effect = [
            {"default_code": {"type": "char"}},
            [{"id": 99, "product_tmpl_id": [55, "T"], "default_code": "SKU"}],
        ]
        with patch("shadowbot.odoo_adapter.xmlrpc.client.ServerProxy", return_value=p):
            c = OdooClient("http://odoo/login?db=db", "u", "p")
            r = c.search_variant_template_id("SKU")
        assert r == 55

    def test_product_exists(self):
        p = MagicMock()
        p.authenticate.return_value = 1
        p.execute_kw.side_effect = [
            {"default_code": {"type": "char"}},
            [{"id": 10, "name": "T", "default_code": "SKU"}],
        ]
        with patch("shadowbot.odoo_adapter.xmlrpc.client.ServerProxy", return_value=p):
            c = OdooClient("http://odoo/login?db=db", "u", "p")
            r = c.product_exists("SKU")
        assert r == 10


def test_parse_url():
    b, d = _parse_odoo_login_url("http://odoo:8069/web/login?db=erp")
    assert b == "http://odoo:8069"
    assert d == "erp"


def test_compact_fault():
    r = _compact_fault(ValueError("test"))
    assert "test" in r