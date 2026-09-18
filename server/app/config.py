from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All settings come from env vars (or server/.env locally); nothing is defaulted.

    See server/.env.example for every key. docker-compose.yml and render.yaml
    set them for Docker and Render.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    redis_url: str | None
    cache_seconds: int
    http_timeout: float
    nominatim_ua: str
    cors_origins: list[str]

    # External services and how politely we call them
    geocode_url: str
    overpass_url: str
    overpass_retry_status: set[int]
    overpass_retry_seconds: float = Field(ge=0)
    # Nominatim's usage policy allows at most one request per second.
    nominatim_pause_seconds: float = Field(ge=0)
    # Nominatim won't return more than 40 results per search.
    nominatim_max_results: int = Field(ge=1, le=40)
    # Bump to invalidate cached discovery results after changing their shape.
    cache_version: str

    @field_validator("redis_url", mode="before")
    @classmethod
    def blank_redis_means_memory_cache(cls, value: str | None) -> str | None:
        """Treat REDIS_URL= (empty) as "no Redis", so the in-memory cache is used."""
        return value or None

    @field_validator("database_url")
    @classmethod
    def use_asyncpg_driver(cls, value: str) -> str:
        """Accept the plain postgres:// URLs that Neon and Render hand out.

        Adds the asyncpg driver, renames libpq's sslmode to asyncpg's ssl, and
        drops channel_binding, which asyncpg doesn't know about.
        """
        for scheme in ("postgres://", "postgresql://"):
            if value.startswith(scheme):
                value = "postgresql+asyncpg://" + value.removeprefix(scheme)
                break
        parts = urlsplit(value)
        query = [
            ("ssl" if key == "sslmode" else key, val)
            for key, val in parse_qsl(parts.query)
            if key != "channel_binding"
        ]
        return urlunsplit(parts._replace(query=urlencode(query)))


settings = Settings()
