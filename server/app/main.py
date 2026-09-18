from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.db import engine
from app.routers import pipeline
from app.services import cache


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    await cache.close()
    await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="Deal Prioritizer", version="1.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    app.include_router(pipeline.router)

    @app.get("/healthz", tags=["health"])
    async def healthz() -> dict[str, bool | str]:
        return {"ok": True, "app": "deal-prioritizer"}

    return app


app = create_app()
