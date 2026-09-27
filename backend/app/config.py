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

    # 数据库
    database_url: str = "postgresql+psycopg://commerce:commerce@localhost:5432/commerce"
    dbos_system_database_url: str = "postgresql+psycopg://commerce:commerce@localhost:5432/dbos"

    # 认证 / 加密
    jwt_secret: str
    jwt_expires_minutes: int = 480
    encryption_key: str  # Fernet key，使用 base64-url 编码的 32 字节密钥

    # 运行时
    environment: str = "dev"
    log_level: str = "INFO"
    responsibility_state_path: str = ".runtime/responsibility.sqlite3"

    # Inbox relay（WP4；字段名由 .env.example 固定）
    inbox_poll_interval_ms: int = 500
    inbox_batch_size: int = 50
    inbox_lease_seconds: int = 30
    inbox_max_attempts: int = 10
    effect_max_retries: int = 3

    # 隐私（WP4：HMAC pseudonymization key + retention window）
    pii_hash_key: str = ""
    privacy_retention_days: int = 30
    privacy_cleanup_interval_hours: int = 24

    # 最小权限数据库角色（WP3/WP4）：API 与 worker 在配置时使用
    # 独立 URL；未配置时回退到 database_url。
    api_database_url: str | None = None
    worker_database_url: str | None = None

    # Worker 进程（WP3 contract：9101 暴露 /metrics，并维护 heartbeat）
    worker_metrics_port: int = 9101
    worker_heartbeat_interval_seconds: int = 10

    # Shopify 开发商店
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

    # 可观测性
    otlp_endpoint: str = ""
    raw_payload_retention_days: int = 30

    # Dev-store / sandbox 安全护栏：Shopify refundCreate 绝不能触及真实
    # money unless this is explicitly enabled (plan 二.4 compensation is
    # 人类资产，绝不自动执行；默认 fail-closed）。
    allow_dev_refund: bool = False


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings singleton."""
    return Settings()


__all__ = ["Settings", "get_settings"]
