"""Security primitives: Fernet encryption, webhook HMAC, JWT tokens."""

from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import timedelta
from typing import Any

import jwt
from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings
from app.core.errors import PermissionDeniedError, ValidationError
from app.core.time import utc_now

JWT_ALGORITHM = "HS256"
JWT_ISSUER = "commerce-orchestrator"


def _fernet() -> Fernet:
    return Fernet(get_settings().encryption_key)


def encrypt_payload(raw: bytes) -> bytes:
    """Encrypt a raw payload (e.g. an ingested webhook body) with Fernet."""
    return _fernet().encrypt(raw)


def decrypt_payload(token: bytes) -> bytes:
    """Decrypt a Fernet token, raising ValidationError on tampered input."""
    try:
        return _fernet().decrypt(token)
    except InvalidToken as exc:
        raise ValidationError("Encrypted payload could not be decrypted") from exc


def verify_webhook_hmac(payload: bytes, signature_header: str, secret: str) -> bool:
    """Constant-time HMAC-SHA256 verification for Shopify-style webhooks.

    ``signature_header`` is the base64 digest sent in the webhook signature
    header (e.g. ``X-Shopify-Hmac-Sha256``).
    """
    expected = base64.b64encode(
        hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    ).decode("ascii")
    return hmac.compare_digest(expected, signature_header or "")


def encode_jwt(
    subject: str,
    roles: list[str],
    *,
    expires_minutes: int | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Encode a JWT carrying role claims (role strings from ROLES)."""
    settings = get_settings()
    now = utc_now()
    claims: dict[str, Any] = {
        "sub": subject,
        "roles": roles,
        "iat": now,
        "exp": now + timedelta(minutes=expires_minutes or settings.jwt_expires_minutes),
        "iss": JWT_ISSUER,
    }
    if extra_claims:
        claims.update(extra_claims)
    return jwt.encode(claims, settings.jwt_secret, algorithm=JWT_ALGORITHM)


def decode_jwt(token: str) -> dict[str, Any]:
    """Decode and verify a JWT; raises PermissionDeniedError when invalid."""
    try:
        return jwt.decode(
            token,
            get_settings().jwt_secret,
            algorithms=[JWT_ALGORITHM],
            issuer=JWT_ISSUER,
        )
    except jwt.PyJWTError as exc:
        raise PermissionDeniedError("Invalid or expired token") from exc


__all__ = [
    "JWT_ALGORITHM",
    "JWT_ISSUER",
    "decode_jwt",
    "decrypt_payload",
    "encode_jwt",
    "encrypt_payload",
    "verify_webhook_hmac",
]
