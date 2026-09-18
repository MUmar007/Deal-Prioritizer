import asyncio
import os
from collections.abc import AsyncIterator

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://deal:deal@localhost:5433/dealprioritizer_test",
)

for key, value in {
    "DATABASE_URL": TEST_DATABASE_URL,
    "REDIS_URL": "",
    "CACHE_SECONDS": "3600",
    "HTTP_TIMEOUT": "15",
    "NOMINATIM_UA": "DealPrioritizer/1.0 (tests)",
    "CORS_ORIGINS": '["http://localhost:3000"]',
}.items():
    os.environ.setdefault(key, value)

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db import Base, get_session
from app.main import app


async def _ensure_database(url: str) -> None:
    target = make_url(url)
    conn = await asyncpg.connect(
        user=target.username,
        password=target.password,
        host=target.host,
        port=target.port,
        database="postgres",
    )
    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", target.database
        )
        if not exists:
            await conn.execute(f'CREATE DATABASE "{target.database}"')
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def database_url() -> str:
    try:
        asyncio.run(_ensure_database(TEST_DATABASE_URL))
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"Postgres not reachable for tests ({exc})")
    return TEST_DATABASE_URL


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client(database_url: str) -> AsyncIterator[AsyncClient]:
    engine = create_async_engine(database_url, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.fixture
async def offline_client() -> AsyncIterator[AsyncClient]:
    """For requests that should fail before they ever reach the database."""

    async def no_session() -> AsyncIterator[None]:
        yield None

    app.dependency_overrides[get_session] = no_session
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()
