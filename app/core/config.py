from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="TC_", extra="ignore")

    app_name: str = "Transparent Company MVP+"
    env: str = "dev"
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    database_url: str = "sqlite+pysqlite:///./tc.db"
    test_database_url: str = "sqlite+pysqlite:///:memory:"

    bootstrap_demo_on_startup: bool = False

    # Agent memory backend: openviking_http | local
    agent_memory_backend: str = "openviking_http"
    openviking_base_url: str = "http://openviking:1933"
    openviking_api_key: str | None = None
    openviking_timeout_seconds: int = 15
    openviking_auto_commit: bool = True
    openviking_fallback_local: bool = True

    agent_signing_key: str = Field(
        default="Lw8iRr6HF9qf8Bk6Y2mJxZTAWGFvPn8qWqv4HP47jtk=",
        description="Base64 Ed25519 seed (32 bytes)",
    )
    human_signing_key: str = Field(
        default="2+JBy7Brjq4j3P5foY3UbcwG0CC3Q1wId4C+LkQYL8o=",
        description="Base64 Ed25519 seed (32 bytes)",
    )
    auditor_signing_key: str = Field(
        default="6f5N38fH44g6LgHEsIwlLccVYTi9J6fWf9eG7Y6xkWw=",
        description="Base64 Ed25519 seed (32 bytes)",
    )

    anchor_mode: str = "immudb_py"
    immudb_address: str = "immudb"
    immudb_port: int = 3322
    immudb_user: str = "immudb"
    immudb_password: str = "immudb"
    immudb_database: str = "defaultdb"
    immuclient_bin: str = "immuclient"

    receipt_backend: str = "minio"
    receipts_dir: Path = Path("/tmp/receipts")
    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "receipts"
    minio_secure: bool = False

    reveal_token_ttl_seconds: int = 600

    superset_admin_user: str = "admin"
    superset_admin_password: str = "admin"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
