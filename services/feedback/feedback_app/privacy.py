import re

EMAIL = re.compile(
    r"\b[A-Za-z0-9._%+-]{1,64}@"
    r"[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63})*"
    r"\.[A-Za-z]{2,63}\b"
)
PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
SECRET = re.compile(r"(?i)(api[_-]?key|token|secret)\s*[:=]\s*\S+")  # NOSONAR


def sanitize_message(message: str) -> str:
    message = EMAIL.sub("[邮箱已脱敏]", message)
    message = PHONE.sub("[手机号已脱敏]", message)
    return SECRET.sub("[密钥已脱敏]", message)

