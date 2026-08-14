from feedback_app.privacy import sanitize_message


def test_sensitive_patterns_are_removed_before_persistence() -> None:
    result = sanitize_message("联系 13812345678 或 user@example.com，token=abc123")
    assert "13812345678" not in result
    assert "user@example.com" not in result
    assert "abc123" not in result

