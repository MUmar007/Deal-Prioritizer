from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Every setting can be overridden with an env var or a .env file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://deal:deal@localhost:5433/dealprioritizer"
    redis_url: str | None = None
    cache_seconds: int = 3600
    http_timeout: float = 15.0
    nominatim_ua: str = "DealPrioritizer/1.0"
    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:4173",
    ]

    @field_validator("database_url")
    @classmethod
    def use_asyncpg_driver(cls, value: str) -> str:
        """Add the asyncpg driver to plain postgres:// URLs (Render hands those out)."""
        for scheme in ("postgres://", "postgresql://"):
            if value.startswith(scheme):
                return "postgresql+asyncpg://" + value.removeprefix(scheme)
        return value


settings = Settings()
