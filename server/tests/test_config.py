import pytest

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
