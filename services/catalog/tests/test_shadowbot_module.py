import importlib.util
import inspect
from unittest.mock import patch, MagicMock
from pathlib import Path

# ---------------------------------------------------------------------------
# Load modules (shadowbot is now a real package)
# ---------------------------------------------------------------------------

def _load_module(name, filename):
    path = Path(__file__).parents[1] / 'shadowbot' / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

RPA = _load_module('catalog_odoo_rpa', 'catalog_odoo_rpa.py')
ODOO = _load_module('odoo_adapter', 'odoo_adapter.py')
PB = _load_module('product_builder', 'product_builder.py')

# ---------------------------------------------------------------------------
# catalog_odoo_rpa tests
# ---------------------------------------------------------------------------

def test_operation_key_is_stable_and_separates_recovery_from_failure():
    written = RPA._operation_key('run-1', 'item-1', 'written')
    failed = RPA._operation_key('run-1', 'item-1', 'failed', 'timeout')
    assert written == RPA._operation_key('run-1', 'item-1', 'written')
    assert failed == RPA._operation_key('run-1', 'item-1', 'failed', 'timeout')
    assert written != failed
    assert len(written) <= 128

def test_required_rejects_blank_secret():
    try:
        RPA._required({'rpa_token': '  '}, 'rpa_token')
    except ValueError as exc:
        assert str(exc) == 'missing argument: rpa_token'
    else:
        raise AssertionError('blank secret should be rejected')

def test_shadowbot_entrypoint_has_no_python_defaults():
    parameters = inspect.signature(RPA.run).parameters.values()
    assert all(
        parameter.default is inspect.Parameter.empty
        for parameter in parameters
    )

# ---------------------------------------------------------------------------
# odoo_adapter tests
# ---------------------------------------------------------------------------

def test_parse_odoo_login_url_extracts_origin_and_database():
    base_url, database = ODOO._parse_odoo_login_url(
        'http://127.0.0.1:18069/web/login?db=catalog_erp'
    )
    assert base_url == 'http://127.0.0.1:18069'
    assert database == 'catalog_erp'

def test_parse_odoo_login_url_requires_database():
    try:
        ODOO._parse_odoo_login_url('http://127.0.0.1:18069/web/login')
    except ValueError as exc:
        assert 'missing db' in str(exc)
    else:
        raise AssertionError('database-less Odoo URL should be rejected')

# ---------------------------------------------------------------------------
# product_builder tests
# ---------------------------------------------------------------------------

def test_parse_price_accepts_currency_text_and_blank_values():
    assert PB.parse_price("¥ 1,299.50") == 1299.50
    assert PB.parse_price('') == 0.0

def test_product_type_selection_uses_supported_odoo_value():
    from shadowbot.odoo_adapter import OdooClient

    class FakeOdoo(OdooClient):
        def __init__(self):
            pass
        def fields_get(self, model):
            return {
                'detailed_type': {
                    'readonly': False,
                    'selection': [['service', 'Service'], ['consu', 'Goods']],
                }
            }
        def field_writable(self, model, name):
            return True
        def selection_keys(self, field_def):
            return OdooClient.selection_keys(field_def)

    odoo = FakeOdoo()
    assert PB.pick_product_type_value(odoo) == ('detailed_type', 'consu')

def test_build_product_values_uses_discovered_writable_fields():
    from shadowbot.odoo_adapter import OdooClient

    class FakeOdoo(OdooClient):
        def __init__(self):
            pass
        def fields_get(self, model):
            return {
                'name': {'readonly': False},
                'default_code': {'readonly': False},
                'list_price': {'readonly': False},
                'description_sale': {'readonly': False},
                'sale_ok': {'readonly': False},
                'purchase_ok': {'readonly': True},
                'detailed_type': {
                    'readonly': False,
                    'selection': [['consu', 'Goods']],
                },
            }
        def field_writable(self, model, name):
            fields = self.fields_get(model)
            if name not in fields:
                return False
            return not bool(fields[name].get('readonly'))

    odoo = FakeOdoo()
    values = PB.build_product_values(
        odoo,
        {
            'item_id': 'item-1',
            'sku': 'TEST-001',
            'listing_title': 'Test product',
            'price': '¥99.50',
            'category': 'test',
            'stock': '10',
            'selling_points': 'stable',
            'keywords': 'test',
        },
        'full',
    )
    assert values['default_code'] == 'TEST-001'
    assert values['list_price'] == 99.50
    assert values['detailed_type'] == 'consu'
    assert values['sale_ok'] is True
    assert 'purchase_ok' not in values
# ---------------------------------------------------------------------------
# odoo_adapter: _compact_fault
# ---------------------------------------------------------------------------

def test_compact_fault_extracts_fault_string():
    class FakeFault(Exception):
        faultString = "Connection refused\nTimeout"

    result = ODOO._compact_fault(FakeFault("extra"))
    assert "Connection refused" in result
    assert "Timeout" in result

def test_compact_fault_falls_back_to_str():
    result = ODOO._compact_fault(ValueError("plain error"))
    assert "plain error" in result

# ---------------------------------------------------------------------------
# odoo_adapter: selection_keys (static method)
# ---------------------------------------------------------------------------

def test_selection_keys_extracts_choice_keys():
    field_def = {"selection": [["a", "Alpha"], ["b", "Beta"]]}
    assert ODOO.OdooClient.selection_keys(field_def) == ["a", "b"]

def test_selection_keys_missing_key_returns_empty():
    assert ODOO.OdooClient.selection_keys({}) == []

# ---------------------------------------------------------------------------
# product_builder: _item_value
# ---------------------------------------------------------------------------

def test_item_value_returns_stripped_string():
    assert PB._item_value({"a": "  hello  "}, "a") == "hello"

def test_item_value_missing_key_returns_empty():
    assert PB._item_value({}, "missing") == ""

def test_item_value_none_value_returns_empty():
    assert PB._item_value({"a": None}, "a") == ""

def test_item_value_custom_default():
    assert PB._item_value({}, "missing", "fallback") == "fallback"

# ---------------------------------------------------------------------------
# product_builder: build_note
# ---------------------------------------------------------------------------

def test_build_note_includes_all_fields():
    note = PB.build_note({
        "sku": "SKU-001",
        "category": "Test",
        "stock": "10",
        "selling_points": "fast",
        "keywords": "test",
        "item_id": "item-1",
    })
    assert "SKU-001" in note
    assert "Test" in note
    assert "10" in note
    assert "fast" in note

# ---------------------------------------------------------------------------
# product_builder: _set_if_writable
# ---------------------------------------------------------------------------

def test_set_if_writable_sets_when_field_is_writable():
    from shadowbot.odoo_adapter import OdooClient
    class FakeOdoo(OdooClient):
        def __init__(self): pass
        def field_writable(self, model, name):
            return name == "writable_field"

    odoo = FakeOdoo()
    values = {}
    PB._set_if_writable(odoo, "product.template", values, "writable_field", "hello")
    PB._set_if_writable(odoo, "product.template", values, "readonly_field", "world")
    assert values == {"writable_field": "hello"}

# ---------------------------------------------------------------------------
# product_builder: _enrich_full_mode_values
# ---------------------------------------------------------------------------

def test_enrich_full_mode_values_adds_type_and_descriptions():
    from shadowbot.odoo_adapter import OdooClient
    class FakeOdoo(OdooClient):
        def __init__(self): pass
        def fields_get(self, model):
            return {
                "detailed_type": {
                    "readonly": False,
                    "selection": [["consu", "Goods"]],
                },
                "description_sale": {"readonly": False},
                "description": {"readonly": False},
                "sale_ok": {"readonly": False},
                "purchase_ok": {"readonly": True},
            }
        def field_writable(self, model, name):
            return not bool(self.fields_get(model).get(name, {}).get("readonly"))

    odoo = FakeOdoo()
    values = {}
    PB._enrich_full_mode_values(odoo, values, "test note")
    assert values["detailed_type"] == "consu"
    assert values["description_sale"] == "test note"
    assert values["description"] == "test note"
    assert values["sale_ok"] is True
    assert "purchase_ok" not in values

# ---------------------------------------------------------------------------
# product_builder: write_template_values
# ---------------------------------------------------------------------------

def test_write_template_values_calls_odoo_execute():
    from shadowbot.odoo_adapter import OdooClient
    class FakeOdoo(OdooClient):
        def __init__(self): self.calls = []
        def execute(self, model, method, args, kwargs=None):
            self.calls.append((model, method, args))

    odoo = FakeOdoo()
    assert PB.write_template_values(odoo, 42, {"name": "test"})
    assert ("product.template", "write", [[42], {"name": "test"}]) in odoo.calls

def test_write_template_values_empty_returns_false():
    from shadowbot.odoo_adapter import OdooClient
    class FakeOdoo(OdooClient):
        def __init__(self): pass
    assert PB.write_template_values(FakeOdoo(), 1, {}) is False

# ---------------------------------------------------------------------------
# product_builder: write_variant_sku
# ---------------------------------------------------------------------------

def test_write_variant_sku_updates_variant_code():
    from shadowbot.odoo_adapter import OdooClient
    class FakeOdoo(OdooClient):
        def __init__(self): self.calls = []
        def fields_get(self, model): return {"default_code": {"readonly": False}}
        def field_writable(self, model, name): return True
        def execute(self, model, method, args, kwargs=None):
            self.calls.append((model, method, args))
            if method == "search_read":
                return [{"id": 99, "default_code": "OLD"}]
            return True

    odoo = FakeOdoo()
    assert PB.write_variant_sku(odoo, 42, "NEW-SKU")
    assert any("write" in str(c) and "NEW-SKU" in str(c) for c in odoo.calls)

def test_write_variant_sku_readonly_field_returns_false():
    from shadowbot.odoo_adapter import OdooClient
    class FakeOdoo(OdooClient):
        def __init__(self): pass
        def fields_get(self, model): return {"default_code": {"readonly": True}}
        def field_writable(self, model, name): return False

    assert PB.write_variant_sku(FakeOdoo(), 1, "SKU") is False

# ---------------------------------------------------------------------------
# product_builder: after_create_update_optional_fields
# ---------------------------------------------------------------------------

def test_after_create_calls_write_with_optional_fields():
    from shadowbot.odoo_adapter import OdooClient
    class FakeOdoo(OdooClient):
        def __init__(self): self.write_calls = []
        def fields_get(self, model):
            return {
                "default_code": {"readonly": False},
                "list_price": {"readonly": False},
                "description_sale": {"readonly": False},
                "sale_ok": {"readonly": False},
                "detailed_type": {"readonly": False, "selection": [["consu", "Goods"]]},
            }
        def field_writable(self, model, name):
            return name in self.fields_get(model)
        def execute(self, model, method, args, kwargs=None):
            if method == "write":
                self.write_calls.append(args)
            if method == "search_read":
                return [{"id": 99}]
            return True

    odoo = FakeOdoo()
    PB.after_create_update_optional_fields(odoo, 42, {
        "sku": "SKU-001", "price": "99.50", "category": "test",
        "stock": "10", "selling_points": "fast", "keywords": "k",
        "item_id": "i1",
    })
    assert len(odoo.write_calls) >= 1

# ---------------------------------------------------------------------------
# product_builder: create_template_with_fallback
# ---------------------------------------------------------------------------

def test_create_with_fallback_succeeds_on_first_attempt():
    from shadowbot.odoo_adapter import OdooClient
    class FakeOdoo(OdooClient):
        def __init__(self): self.creates = []
        def fields_get(self, model): return {"name": {"readonly": False}, "default_code": {"readonly": False}, "detailed_type": {"readonly": False, "selection": [["consu", "Goods"]]}}
        def field_writable(self, model, name): return True
        def execute(self, model, method, args, kwargs=None):
            if method == "create":
                self.creates.append(args)
                return 11
            return True

    odoo = FakeOdoo()
    result = PB.create_template_with_fallback(odoo, {
        "sku": "S", "listing_title": "t", "price": "10",
        "category": "c", "stock": "5", "selling_points": "sp", "keywords": "k",
        "item_id": "i",
    })
    assert result == 11
    assert len(odoo.creates) == 1

def test_create_with_fallback_tries_multiple_attempts():
    from shadowbot.odoo_adapter import OdooClient
    class FakeOdoo(OdooClient):
        def __init__(self): self.creates = 0
        def fields_get(self, model): return {"name": {"readonly": False}, "default_code": {"readonly": False}, "detailed_type": {"readonly": False, "selection": [["consu", "Goods"]]}}
        def field_writable(self, model, name): return True
        def execute(self, model, method, args, kwargs=None):
            if method == "create":
                self.creates += 1
                if self.creates < 3:
                    raise RuntimeError("simulated failure")
                return 22
            return True

    odoo = FakeOdoo()
    result = PB.create_template_with_fallback(odoo, {
        "sku": "S", "listing_title": "t", "price": "10",
        "category": "c", "stock": "5", "selling_points": "sp", "keywords": "k",
        "item_id": "i",
    })
    assert result == 22
    assert odoo.creates == 3

# ---------------------------------------------------------------------------
# product_builder: create_product
# ---------------------------------------------------------------------------

def test_create_product_returns_existing_id_when_found():
    from shadowbot.odoo_adapter import OdooClient
    class FakeOdoo(OdooClient):
        def __init__(self): pass
        def product_exists(self, sku): return 55

    result = PB.create_product(FakeOdoo(), {"sku": "EXISTING", "listing_title": "t", "price": "10", "category": "c", "stock": "1", "selling_points": "s", "keywords": "k", "item_id": "i"})
    assert result == "odoo-existing-55"

def test_create_product_creates_new_when_not_found():
    from shadowbot.odoo_adapter import OdooClient
    class FakeOdoo(OdooClient):
        def __init__(self): self.creates = []
        def fields_get(self, model): return {"name": {"readonly": False}, "default_code": {"readonly": False}, "detailed_type": {"readonly": False, "selection": [["consu", "Goods"]]}}
        def field_writable(self, model, name): return True
        def product_exists(self, sku): return None
        def execute(self, model, method, args, kwargs=None):
            if method == "create":
                self.creates.append(args)
                return 33
            return True

    result = PB.create_product(FakeOdoo(), {"sku": "NEW", "listing_title": "t", "price": "10", "category": "c", "stock": "1", "selling_points": "s", "keywords": "k", "item_id": "i"})
    assert result == "odoo-product-33"  # create_product returns odoo-product-{id} for new products
# ---------------------------------------------------------------------------
# catalog_odoo_rpa: HTTP helpers
# ---------------------------------------------------------------------------


def test_request_bytes_returns_body():
    mock_response = MagicMock()
    mock_response.__enter__.return_value = mock_response
    mock_response.read.return_value = b'[{"item_id": "1"}]'
    with patch("urllib.request.urlopen", return_value=mock_response):
        result = RPA._request_bytes("http://api/test/items?run_id=r1", "token123", 10)
    assert result == b'[{"item_id": "1"}]'

def test_request_json_returns_parsed():
    mock_response = MagicMock()
    mock_response.__enter__.return_value = mock_response
    mock_response.read.return_value = b'{"status": "ok"}'
    with patch("urllib.request.urlopen", return_value=mock_response):
        result = RPA._request_json("http://api/submit", "token", {"a": 1}, 10)
    assert result == {"status": "ok"}

def test_approved_items_parses_response():
    mock_response = MagicMock()
    mock_response.__enter__.return_value = mock_response
    mock_response.read.return_value = b'item_id,approved\na,true\nb,false\nc,true\n'
    with patch("urllib.request.urlopen", return_value=mock_response):
        items = RPA._approved_items("http://base", "r1", "token")
    # CSV DictReader converts bool strings and strips whitespace
    assert items == [
        {"item_id": "a", "approved": "true"},
        {"item_id": "b", "approved": "false"},
        {"item_id": "c", "approved": "true"},
    ]

def test_approved_items_skips_non_list():
    mock_response = MagicMock()
    mock_response.__enter__.return_value = mock_response
    mock_response.read.return_value = b'{"error": "not found"}'
    with patch("urllib.request.urlopen", return_value=mock_response):
        items = RPA._approved_items("http://base", "r1", "token")
    # CSV DictReader converts bool strings and strips whitespace
    assert items == []

def test_callback_posts_json():
    mock_response = MagicMock()
    mock_response.__enter__.return_value = mock_response
    mock_response.read.return_value = b'{"ack": true}'
    with patch("urllib.request.urlopen", return_value=mock_response):
        RPA._callback("http://base", "r1", "token", {"item_id": "1"}, "written", record_id=42)

# ---------------------------------------------------------------------------
# catalog_odoo_rpa: main() orchestrator test
# ---------------------------------------------------------------------------


def test_main_dry_run_returns_approved_count():
    # Dry run mode skips Odoo
    mock_response = MagicMock()
    mock_response.__enter__.return_value = mock_response
    mock_response.read.return_value = b"item_id,approved\nitem-1,true\nitem-2,false\nitem-3,true\n"
    with patch("urllib.request.urlopen", return_value=mock_response):
        result = RPA.main({
            "api_base_url": "http://api",
            "run_id": "r1",
            "rpa_token": "t",
            "odoo_login_url": "http://odoo/login?db=db",
            "odoo_product_list_url": "http://odoo/list",
            "odoo_username": "u",
            "odoo_password": "p",
            "dry_run": "true",
        })
    assert result["approved"] == 3
    assert result["dry_run"] is True


