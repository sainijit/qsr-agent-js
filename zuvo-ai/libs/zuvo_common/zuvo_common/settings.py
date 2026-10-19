"""Environment-only settings. Secrets are never read from tracked files."""
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = Field("zuvo", alias="SERVICE_NAME")
    app_env: str = Field("development", alias="APP_ENV")
    log_level: str = Field("INFO", alias="LOG_LEVEL")

    database_url: str = Field(
        "postgresql+asyncpg://zuvo_app:zuvo_app@localhost:5432/zuvo_db", alias="DATABASE_URL"
    )
    redis_url: str = Field("redis://localhost:6379/0", alias="REDIS_URL")
    kafka_bootstrap_servers: str = Field("localhost:9092", alias="KAFKA_BOOTSTRAP_SERVERS")

    tenant_routing_path: str = Field("config/tenant-routing.yaml", alias="TENANT_ROUTING_PATH")
    tenant_policy_root: str = Field(".", alias="TENANT_POLICY_ROOT")

    meta_verify_token: str = Field("", alias="META_VERIFY_TOKEN")
    meta_app_secret: str = Field("", alias="META_APP_SECRET")
    meta_system_user_token: str = Field("", alias="META_SYSTEM_USER_TOKEN")
    meta_api_version: str = Field("v20.0", alias="META_API_VERSION")
    meta_graph_base_url: str = Field("https://graph.facebook.com", alias="META_GRAPH_BASE_URL")
    meta_dry_run: bool = Field(True, alias="META_DRY_RUN")
    require_hmac_verification: bool = Field(True, alias="REQUIRE_HMAC_VERIFICATION")

    jwt_secret: str = Field("", alias="JWT_SECRET")
    jwt_ttl_seconds: int = Field(8 * 3600, alias="JWT_TTL_SECONDS")
    auth_dev_login: bool = Field(False, alias="AUTH_DEV_LOGIN")
    enable_simulator: bool = Field(False, alias="ENABLE_SIMULATOR")

    gemini_api_key: str = Field("", alias="GEMINI_API_KEY")
    gemini_model: str = Field("gemini-3.5-flash-lite", alias="ZUVO_GEMINI_MODEL")
    gemini_timeout_seconds: float = Field(20.0, alias="GEMINI_TIMEOUT_SECONDS")
    gemini_max_attempts: int = Field(3, alias="GEMINI_MAX_ATTEMPTS")

    rag_url: str = Field("", alias="RAG_URL")  # e.g. http://zuvo-ai-rag:8020; empty disables RAG retrieval
    rag_service_token: str = Field("", alias="RAG_SERVICE_TOKEN")
    rag_timeout_seconds: float = Field(2.0, alias="RAG_TIMEOUT_SECONDS")

    idempotency_ttl_seconds: int = Field(86400, alias="IDEMPOTENCY_TTL_SECONDS")
    allowed_origins: str = Field("http://localhost:3000", alias="ALLOWED_ORIGINS")

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


class UnsafeProductionConfig(RuntimeError):
    pass


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.app_env.strip().lower() == "production":
        from .preflight import runtime_errors

        errs = runtime_errors(s)
        if errs:
            raise UnsafeProductionConfig(f"{s.service_name}: refusing to start with APP_ENV=production: " + "; ".join(errs))
    return s
