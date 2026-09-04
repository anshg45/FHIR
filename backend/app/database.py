"""SQLAlchemy engine / session / schema bootstrap for PostgreSQL."""
import logging

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

logger = logging.getLogger(__name__)

engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency yielding a DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Immutability enforcement: audit_trail + audit_anchors are append-only.
# Any UPDATE / DELETE raises an exception at the DATABASE level, so even a
# direct psql session cannot silently rewrite history.
# ---------------------------------------------------------------------------
IMMUTABILITY_SQL = """
CREATE OR REPLACE FUNCTION aiia_block_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'IMMUTABLE_TABLE: % is append-only. % is not permitted.',
        TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'check_violation';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_audit_trail_no_update ON audit_trail;
CREATE TRIGGER trg_audit_trail_no_update
    BEFORE UPDATE ON audit_trail
    FOR EACH ROW EXECUTE FUNCTION aiia_block_mutation();

DROP TRIGGER IF EXISTS trg_audit_trail_no_delete ON audit_trail;
CREATE TRIGGER trg_audit_trail_no_delete
    BEFORE DELETE ON audit_trail
    FOR EACH ROW EXECUTE FUNCTION aiia_block_mutation();

DROP TRIGGER IF EXISTS trg_audit_trail_no_truncate ON audit_trail;
CREATE TRIGGER trg_audit_trail_no_truncate
    BEFORE TRUNCATE ON audit_trail
    FOR EACH STATEMENT EXECUTE FUNCTION aiia_block_mutation();

DROP TRIGGER IF EXISTS trg_audit_anchors_no_update ON audit_anchors;
CREATE TRIGGER trg_audit_anchors_no_update
    BEFORE UPDATE ON audit_anchors
    FOR EACH ROW EXECUTE FUNCTION aiia_block_mutation();

DROP TRIGGER IF EXISTS trg_audit_anchors_no_delete ON audit_anchors;
CREATE TRIGGER trg_audit_anchors_no_delete
    BEFORE DELETE ON audit_anchors
    FOR EACH ROW EXECUTE FUNCTION aiia_block_mutation();
"""


def install_immutability_triggers() -> None:
    with engine.begin() as conn:
        conn.execute(text(IMMUTABILITY_SQL))
    logger.info("Audit immutability triggers installed")


def init_db() -> None:
    """Create all tables and install DB-level protections. Idempotent."""
    from . import models  # noqa: F401  (side-effect import registers SQLAlchemy mappers)

    Base.metadata.create_all(bind=engine)
    install_immutability_triggers()


def set_audit_triggers(enabled: bool) -> None:
    """Demo-only helper used by the tamper-simulation endpoint."""
    state = "ENABLE" if enabled else "DISABLE"
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE audit_trail {state} TRIGGER trg_audit_trail_no_update"))
        conn.execute(text(f"ALTER TABLE audit_trail {state} TRIGGER trg_audit_trail_no_delete"))
