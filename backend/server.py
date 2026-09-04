"""AIIA CTMS FastAPI entrypoint.

Backend-only application (no UI). Exposes REST + FHIR R4 endpoints on
0.0.0.0:8001, backed by PostgreSQL. Every mutating call is written to an
append-only audit trail with SHA-256 hash-chaining and periodic Merkle-root
anchoring (LOCAL or POLYGON_AMOY).
"""
import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.database import init_db
from app.jobs import ensure_default_thresholds, shutdown_scheduler, start_scheduler
from app.routers import (
    ae,
    alerts,
    audit_router,
    auth,
    deviations,
    exports,
    fhir,
    milestones,
    monitoring,
    patients,
    queries,
    sites,
    studies,
    users,
    visits,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("aiia.ctms")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s v%s", settings.APP_NAME, settings.APP_VERSION)
    logger.info("DATABASE_URL host=%s", settings.DATABASE_URL.split("@")[-1])
    init_db()
    ensure_default_thresholds()
    start_scheduler()
    logger.info("Anchor mode: %s", settings.ANCHOR_MODE)
    yield
    logger.info("Shutting down scheduler")
    shutdown_scheduler()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=(
        "Backend API for the AIIA Clinical Trial Management System. "
        "Implements ICH-GCP / NDCT Rules 2019 style regulatory gates, "
        "an append-only audit trail with Merkle-anchored tamper evidence, "
        "and a FHIR R4 interoperability surface."
    ),
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.CORS_ORIGINS.split(",")] or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------- exception handlers
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    detail = exc.detail
    if isinstance(detail, dict):
        payload = {"error": detail}
    else:
        payload = {"error": {"message": str(detail), "status": exc.status_code}}
    return JSONResponse(status_code=exc.status_code, content=payload)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"error": {"message": "Validation failed", "details": exc.errors()}},
    )


# ---------------------------------------------------------------- root/meta
api = APIRouter(prefix="/api")


@api.get("/", tags=["Meta"])
def api_root():
    return {
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "ok",
        "anchor_mode": settings.ANCHOR_MODE,
        "docs": "/api/docs",
        "fhir_metadata": "/api/fhir/metadata",
    }


@api.get("/health", tags=["Meta"])
def health():
    from sqlalchemy import text

    from app.database import engine

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
        db_err = None
    except Exception as exc:  # noqa: BLE001
        db_ok = False
        db_err = str(exc)
    return {
        "status": "ok" if db_ok else "degraded",
        "database": {"connected": db_ok, "error": db_err},
        "anchor_mode": settings.ANCHOR_MODE,
        "app_version": settings.APP_VERSION,
    }


# ---------------------------------------------------------------- register
api.include_router(auth.router)
api.include_router(users.router)
api.include_router(sites.router)
api.include_router(studies.router)
api.include_router(milestones.router)
api.include_router(patients.router)
api.include_router(visits.router)
api.include_router(deviations.router)
api.include_router(queries.router)
api.include_router(monitoring.router)
api.include_router(ae.router)
api.include_router(alerts.router)
api.include_router(audit_router.router)
api.include_router(exports.router)
api.include_router(fhir.router)

app.include_router(api)
