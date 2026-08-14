from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./catalog.db"
    dify_base_url: str = "http://host.docker.internal:18080/v1"
    dify_catalog_workflow_api_key: str = ""
    dify_timeout_seconds: float = 60.0
    catalog_workflow_version: str = "catalog-copy-v1-candidate"
    allow_demo_copywriter: bool = True
    rpa_token: str = "change-me"
    max_csv_bytes: int = 1_000_000
    max_csv_rows: int = 100


@lru_cache
def get_settings() -> Settings:
    return Settings()
