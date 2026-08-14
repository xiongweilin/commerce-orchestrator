from feedback_app.evidence import locate_quote


def test_exact_quote_is_located_in_original_text() -> None:
    message = "你好，我们整个项目组都收不到到期提醒，请帮忙。"
    result = locate_quote(message, "我们整个项目组都收不到到期提醒")
    assert result is not None
    assert message[result.start : result.end] == result.quote
    assert result.match_method == "exact"


def test_nfkc_and_whitespace_match_maps_back_to_original_offsets() -> None:
    message = "导入后，ＡＢＣ  字段\n没有映射。"
    result = locate_quote(message, "ABC 字段 没有映射。")
    assert result is not None
    assert result.match_method == "normalized"
    assert message[result.start : result.end] == result.quote


def test_missing_quote_is_not_fabricated() -> None:
    assert locate_quote("只有真实证据", "不存在的证据") is None

