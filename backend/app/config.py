"""Application settings.

All settings are read from environment variables prefixed with ``COMMERCE_``
(e.g. ``COMMERCE_DATABASE_URL``). Use :func:`get_settings` for a cached
singleton; never instantiate :class:`Settings` directly.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the operations control tower backend."""

    model_config = SettingsConfigDict(
        env_prefix="COMMERCE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    database_url: str = "postgresql+psycopg://commerce:commerce@localhost:5432/commerce"
    dbos_system_database_url: str = "postgresql+psycopg://commerce:commerce@localhost:5432/dbos"

    # Auth / encryption
    jwt_secret: str
    jwt_expires_minutes: int = 480
    encryption_key: str  # Fernet key, base64-url encoded 32-byte key

    # Runtime
    environment: str = "dev"
    log_level: str = "INFO"

    # Shopify dev store
    shopify_api_version: str = "2026-07"
    shopify_shop_name: str = ""
    shopify_access_token: str = ""
    shopify_client_id: str = ""
    shopify_client_secret: str = ""
    shopify_webhook_secret: str = ""

    # Odoo 19
    odoo_base_url: str = ""
    odoo_api_key: str = ""
    odoo_db: str = ""
    odoo_username: str = ""

    # Dify workflow LLM（P6：LLM 只生成建议，不批准不执行）
    dify_base_url: str = "http://127.0.0.1:18080"
    dify_workflow_id: str = ""
    dify_api_key: str = ""

    # Observability
    otlp_endpoint: str = ""
    raw_payload_retention_days: int = 30


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings singleton."""
    return Settings()


__all__ = ["Settings", "get_settings"]
