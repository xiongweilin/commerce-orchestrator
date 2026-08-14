from pathlib import Path

ROOT = Path(__file__).parents[1]
PORTFOLIO = ROOT / "portfolio"


def test_catalog_portfolio_has_navigation_and_evidence_assets() -> None:
    html = (PORTFOLIO / "index.html").read_text(encoding="utf-8")

    assert 'href="/index"' in html
    assert 'href="/feedback"' in html
    assert 'href="/metratio.png"' in html

    for filename, magic in (
        ("odoo-products.jpg", b"\xff\xd8\xff"),
        ("metabase-dashboard.jpg", b"\xff\xd8\xff"),
        ("shadowbot-app.png", b"\x89PNG\r\n\x1a\n"),
    ):
        path = PORTFOLIO / "assets" / filename
        assert path.read_bytes().startswith(magic)
        assert f"/catalog-ops/assets/{filename}" in html


def test_catalog_portfolio_keeps_current_state_separate_from_attempt_history() -> None:
    html = (PORTFOLIO / "index.html").read_text(encoding="utf-8")

    assert "当前 ERP 写入错误 0 条" in html
    assert "23 次调试阶段 failed" in html
    assert "不等于当前仍有 23 个失败商品" in html
