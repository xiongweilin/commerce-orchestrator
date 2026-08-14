from pathlib import Path

ROOT = Path(__file__).parents[1]
APP_ICON = ROOT / "web" / "app" / "icon.png"
PORTFOLIO = ROOT / "portfolio" / "index.html"
SHELL = ROOT / "web" / "components" / "Shell.tsx"


def test_shared_next_app_icon_is_a_real_png() -> None:
    """The root app icon must cover every /feedback route."""
    assert APP_ICON.exists()
    content = APP_ICON.read_bytes()
    assert content.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(content) > 1_000


def test_static_portfolio_declares_the_same_site_icon() -> None:
    html = PORTFOLIO.read_text(encoding="utf-8")
    assert '<link rel="icon" href="/metratio.png" type="image/png">' in html


def test_static_portfolio_uses_navigation_for_the_catalog_project() -> None:
    html = PORTFOLIO.read_text(encoding="utf-8")

    assert 'href="/index"' in html
    assert 'href="/catalog-ops"' in html
    assert "补充作品：商品上架运营自动化" not in html


def test_interactive_shell_exposes_portfolio_navigation() -> None:
    source = SHELL.read_text(encoding="utf-8")

    assert '["/index", "作品首页"]' in source
    assert '["/catalog-ops", "商品自动化"]' in source
