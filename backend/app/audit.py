"""Audit trail service: append-only writes with a SHA-256 hash chain."""
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .crypto_utils import GENESIS_HASH, canonical_json, compute_row_hash
from .models import AuditTrail, User

logger = logging.getLogger(__name__)

SCALAR_TYPES = (str, int, float, bool, type(None))


def snapshot(obj: Any, fields: list[str] | None = None) -> dict | None:
    """JSON-safe snapshot of a SQLAlchemy model instance."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return {k: _safe(v) for k, v in obj.items()}
    mapper = getattr(obj, "__mapper__", None)
    if mapper is None:
        return {"value": _safe(obj)}
    cols = [c.key for c in mapper.column_attrs]
    if fields:
        cols = [c for c in cols if c in fields]
    return {c: _safe(getattr(obj, c, None)) for c in cols}


def _safe(value):
    from datetime import date
    from decimal import Decimal

    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, SCALAR_TYPES) or isinstance(value, (list, dict)):
        return value
    return str(value)


def _last_hash(db: Session) -> str:
    row = db.execute(
        text("SELECT row_hash FROM audit_trail ORDER BY id DESC LIMIT 1")
    ).first()
    return row[0] if row else GENESIS_HASH


def log_action(
    db: Session,
    *,
    action: str,
    entity_type: str,
    entity_id: str | None = None,
    user: User | None = None,
    old_value: Any = None,
    new_value: Any = None,
    reason: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    commit: bool = True,
) -> AuditTrail:
    """Append one immutable audit record, chained to the previous one."""
    prev_hash = _last_hash(db)
    ts = datetime.now(timezone.utc)
    old_json = snapshot(old_value) if old_value is not None else None
    new_json = snapshot(new_value) if new_value is not None else None

    row_hash = compute_row_hash(
        prev_hash=prev_hash,
        user_id=user.id if user else None,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        old_value=old_json,
        new_value=new_json,
        timestamp=ts,
    )

    record = AuditTrail(
        user_id=user.id if user else None,
        user_email=user.email if user else None,
        user_role=user.role if user else None,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        old_value=old_json,
        new_value=new_json,
        reason=reason,
        ip_address=ip_address,
        user_agent=(user_agent or "")[:255] or None,
        prev_hash=prev_hash,
        row_hash=row_hash,
        timestamp=ts,
    )
    db.add(record)
    if commit:
        db.commit()
    else:
        db.flush()
    return record


def verify_hash_chain(db: Session, start_id: int | None = None, end_id: int | None = None) -> dict:
    """Recompute the full SHA-256 chain from raw rows and report breaks."""
    stmt = select(AuditTrail).order_by(AuditTrail.id)
    if start_id:
        stmt = stmt.where(AuditTrail.id >= start_id)
    if end_id:
        stmt = stmt.where(AuditTrail.id <= end_id)
    rows = db.execute(stmt).scalars().all()

    broken: list[dict] = []
    expected_prev = None
    for row in rows:
        recomputed = compute_row_hash(
            prev_hash=row.prev_hash,
            user_id=row.user_id,
            action=row.action,
            entity_type=row.entity_type,
            entity_id=row.entity_id,
            old_value=row.old_value,
            new_value=row.new_value,
            timestamp=row.timestamp,
        )
        problems = []
        if recomputed != row.row_hash:
            problems.append("row_hash_mismatch")
        if expected_prev is not None and row.prev_hash != expected_prev:
            problems.append("chain_link_broken")
        if problems:
            broken.append(
                {
                    "audit_id": row.id,
                    "action": row.action,
                    "entity_type": row.entity_type,
                    "entity_id": row.entity_id,
                    "stored_row_hash": row.row_hash,
                    "recomputed_row_hash": recomputed,
                    "problems": problems,
                }
            )
        expected_prev = row.row_hash

    return {
        "rows_checked": len(rows),
        "chain_intact": len(broken) == 0,
        "broken_links": broken,
    }
