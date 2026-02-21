from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_AGENT_SIGNING_KEY = "Lw8iRr6HF9qf8Bk6Y2mJxZTAWGFvPn8qWqv4HP47jtk="
DEFAULT_HUMAN_SIGNING_KEY = "2+JBy7Brjq4j3P5foY3UbcwG0CC3Q1wId4C+LkQYL8o="
DEFAULT_AUDITOR_SIGNING_KEY = "6f5N38fH44g6LgHEsIwlLccVYTi9J6fWf9eG7Y6xkWw="
DEFAULT_TOKEN_SIGNING_SECRET = "transparent-company-dev-token-secret-change-me"
DEFAULT_AGENT_API_KEY = "tc-agent-dev-key"
DEFAULT_HUMAN_API_KEY = "tc-human-dev-key"
DEFAULT_AUDITOR_API_KEY = "tc-auditor-dev-key"
DEFAULT_SYSTEM_API_KEY = "tc-system-dev-key"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="TC_", extra="ignore")

    app_name: str = "Transparent Company MVP+"
    env: str = "dev"
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    database_url: str = "sqlite+pysqlite:///./tc.db"
    test_database_url: str = "sqlite+pysqlite:///:memory:"
    demo_exports_root: Path = Path("/tmp/transparent-company/demo-exports")

    bootstrap_demo_on_startup: bool = False

    # Agent memory backend: openviking_http | local
    agent_memory_backend: str = "openviking_http"
    openviking_base_url: str = "http://openviking:1933"
    openviking_api_key: str | None = None
    openviking_timeout_seconds: int = 15
    openviking_auto_commit: bool = True
    openviking_fallback_local: bool = True

    auth_enabled: bool = True
    agent_api_key: str = DEFAULT_AGENT_API_KEY
    human_api_key: str = DEFAULT_HUMAN_API_KEY
    auditor_api_key: str = DEFAULT_AUDITOR_API_KEY
    system_api_key: str = DEFAULT_SYSTEM_API_KEY
    agent_actor_id: str = "agent-001"
    human_actor_id: str = "human-001"
    auditor_actor_id: str = "auditor-001"
    system_actor_id: str = "system-001"

    agent_signing_key: str = Field(
        default=DEFAULT_AGENT_SIGNING_KEY,
        description="Base64 Ed25519 seed (32 bytes)",
    )
    human_signing_key: str = Field(
        default=DEFAULT_HUMAN_SIGNING_KEY,
        description="Base64 Ed25519 seed (32 bytes)",
    )
    auditor_signing_key: str = Field(
        default=DEFAULT_AUDITOR_SIGNING_KEY,
        description="Base64 Ed25519 seed (32 bytes)",
    )
    token_signing_secret: str = DEFAULT_TOKEN_SIGNING_SECRET

    anchor_mode: str = "immudb_py"
    anchor_strict: bool = True
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

    def model_post_init(self, __context) -> None:
        if self.env.lower() == "dev":
            return

        insecure_items: list[str] = []
        if self.agent_signing_key == DEFAULT_AGENT_SIGNING_KEY:
            insecure_items.append("TC_AGENT_SIGNING_KEY")
        if self.human_signing_key == DEFAULT_HUMAN_SIGNING_KEY:
            insecure_items.append("TC_HUMAN_SIGNING_KEY")
        if self.auditor_signing_key == DEFAULT_AUDITOR_SIGNING_KEY:
            insecure_items.append("TC_AUDITOR_SIGNING_KEY")
        if self.token_signing_secret == DEFAULT_TOKEN_SIGNING_SECRET:
            insecure_items.append("TC_TOKEN_SIGNING_SECRET")
        if self.agent_api_key == DEFAULT_AGENT_API_KEY:
            insecure_items.append("TC_AGENT_API_KEY")
        if self.human_api_key == DEFAULT_HUMAN_API_KEY:
            insecure_items.append("TC_HUMAN_API_KEY")
        if self.auditor_api_key == DEFAULT_AUDITOR_API_KEY:
            insecure_items.append("TC_AUDITOR_API_KEY")
        if self.system_api_key == DEFAULT_SYSTEM_API_KEY:
            insecure_items.append("TC_SYSTEM_API_KEY")

        if insecure_items:
            raise ValueError(
                "insecure default secrets are not allowed outside dev mode; set env vars: "
                + ", ".join(sorted(insecure_items))
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
