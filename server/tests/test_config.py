import pytest
from pydantic import ValidationError

from app.config import Settings


@pytest.mark.parametrize(
    "url",
    [
        "postgres://u:p@host/db",
        "postgresql://u:p@host/db",
        "postgresql+asyncpg://u:p@host/db",
    ],
)
def test_database_url_uses_asyncpg(url):
    assert Settings(database_url=url).database_url == "postgresql+asyncpg://u:p@host/db"


def test_neon_url_is_translated_for_asyncpg():
    url = (
        "postgresql://u:p@ep-cool-1.us-east-2.aws.neon.tech/db"
        "?sslmode=require&channel_binding=require"
    )
    assert Settings(database_url=url).database_url == (
        "postgresql+asyncpg://u:p@ep-cool-1.us-east-2.aws.neon.tech/db?ssl=require"
    )


def test_missing_setting_fails_loudly(monkeypatch):
    monkeypatch.delenv("DATABASE_URL")
    with pytest.raises(ValidationError, match="database_url"):
        Settings(_env_file=None)


def test_empty_redis_url_means_memory_cache(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "")
    assert Settings(_env_file=None).redis_url is None
