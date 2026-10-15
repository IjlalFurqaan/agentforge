"""FastAPI application factory."""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.routes import auth, github, metrics, models, repositories, tasks
from app.core.config import Settings, get_settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging, get_logger
from app.core.ratelimit import build_rate_limiter
from app.core.telemetry import configure_tracing
from app.db.session import dispose_engine, get_engine, init_engine

log = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.validate_for_runtime()
    configure_logging(settings)
    configure_tracing(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        init_engine(settings)
        app.state.rate_limiter = build_rate_limiter(settings)
        worker = None
        if settings.worker_mode == "embedded":
            from app.worker import Worker

            worker = Worker(settings)
            await worker.start()
            log.info("embedded_worker_started", worker_id=worker.worker_id)
        try:
            yield
        finally:
            if worker is not None:
                await worker.stop()
            await dispose_engine()

    app = FastAPI(
        title=f"{settings.app_name} API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=f"{settings.api_prefix}/docs",
        openapi_url=f"{settings.api_prefix}/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Last-Event-ID"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.unbind_contextvars("request_id")
        response.headers["x-request-id"] = request_id
        response.headers["x-content-type-options"] = "nosniff"
        response.headers["referrer-policy"] = "no-referrer"
        if request.url.path != f"{settings.api_prefix}/health":
            log.info("http_request", method=request.method, path=request.url.path,
                     status=response.status_code, duration_ms=round((time.perf_counter() - started) * 1000, 1),
                     request_id=request_id)
        return response

    install_error_handlers(app)

    for router in (auth.router, github.router, repositories.router, tasks.router, metrics.router, models.router):
        app.include_router(router, prefix=settings.api_prefix)

    @app.get(f"{settings.api_prefix}/health", tags=["ops"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(f"{settings.api_prefix}/ready", tags=["ops"])
    async def ready() -> dict[str, str]:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ready", "sandbox": settings.sandbox_backend, "worker": settings.worker_mode}

    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app, excluded_urls=f"{settings.api_prefix}/health")
    except Exception:  # instrumentation is best-effort
        log.warning("otel_fastapi_instrumentation_failed")

    return app
# Run with: uvicorn app.main:create_app --factory
