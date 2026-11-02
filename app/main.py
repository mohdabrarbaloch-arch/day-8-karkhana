"""Karkhana — application entrypoint."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from . import __version__
from .config import get_settings
from .database import SessionLocal, init_db
from .routers import auth, dashboard, jobs, nodes, scheduler
from .seed import seed_if_empty

settings = get_settings()
logger = logging.getLogger("karkhana")

STATIC_DIR = Path(__file__).parent / "static"

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[f"{settings.rate_limit_per_minute}/minute"],
    # Disable during tests so a burst of cases does not trip the limit.
    enabled=settings.environment != "test",
)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    init_db()
    with SessionLocal() as db:
        admin = seed_if_empty(db, allow_seed=settings.allow_demo_seed)
        if admin is not None:
            logger.info("Demo data present. Sign in as %s", admin.email)
    yield


app = FastAPI(
    title=f"{settings.app_name} API",
    version=__version__,
    description=(
        "GPU compute control plane. Gang scheduling with EASY backfill, "
        "capacity-aware placement, and chargeback arithmetic you can check by hand."
    ),
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
)

app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(_request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={"detail": "Too many requests — slow down and try again shortly."},
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


# Baseline security headers. CSP is intentionally strict about scripts because
# the frontend is one hand-written file with no bundler and no inline handlers.
@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    if settings.is_production:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response


app.include_router(auth.router)
app.include_router(nodes.router)
app.include_router(jobs.router)
app.include_router(scheduler.router)
app.include_router(dashboard.router)


@app.get("/api/health", tags=["meta"])
def health() -> dict[str, object]:
    """Liveness plus a couple of facts worth knowing from a curl."""
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": __version__,
        "environment": settings.environment,
        "backfill": settings.enable_backfill,
        "aging_minutes": settings.aging_minutes,
    }


if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    index_file = STATIC_DIR / "index.html"
    if index_file.is_file():
        return FileResponse(index_file)
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"message": "Karkhana API is running. The UI was not found in app/static/."},
    )
