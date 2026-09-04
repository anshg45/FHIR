"""Audit trail read API + Merkle anchoring + tamper verification."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .. import anchoring
from ..audit import verify_hash_chain
from ..config import settings
from ..database import get_db, set_audit_triggers
from ..deps import AuditContext, audit_ctx
from ..models import AuditAnchor, AuditTrail, User
from ..schemas import AuditOut, Paged, TamperSimRequest
from ..security import RequireRoles

router = APIRouter(prefix="/audit", tags=["Audit Trail & Blockchain Anchoring"])
AUDIT_READERS = RequireRoles("admin", "regulator")


@router.get("", response_model=Paged)
def list_audit(
    entity_type: str | None = None,
    entity_id: str | None = None,
    user_id: str | None = None,
    action: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = Query(100, le=1000),
    offset: int = 0,
    db: Session = Depends(get_db),
    _: User = Depends(AUDIT_READERS),
):
    stmt = select(AuditTrail)
    if entity_type:
        stmt = stmt.where(AuditTrail.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(AuditTrail.entity_id == entity_id)
    if user_id:
        stmt = stmt.where(AuditTrail.user_id == user_id)
    if action:
        stmt = stmt.where(AuditTrail.action == action)
    if date_from:
        stmt = stmt.where(AuditTrail.timestamp >= date_from)
    if date_to:
        stmt = stmt.where(AuditTrail.timestamp <= date_to)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = (
        db.execute(stmt.order_by(AuditTrail.id.desc()).limit(limit).offset(offset))
        .scalars()
        .all()
    )
    return Paged(
        total=total, limit=limit, offset=offset,
        items=[AuditOut.model_validate(r).model_dump() for r in rows],
    )


@router.get("/entity/{entity_type}/{entity_id}", response_model=list[AuditOut])
def entity_history(
    entity_type: str,
    entity_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(RequireRoles("admin", "regulator", "pi", "monitor", "ec", "pv")),
):
    """Full change history of a single record (who changed what, when)."""
    rows = (
        db.execute(
            select(AuditTrail)
            .where(AuditTrail.entity_type == entity_type, AuditTrail.entity_id == entity_id)
            .order_by(AuditTrail.id)
        )
        .scalars()
        .all()
    )
    return [AuditOut.model_validate(r) for r in rows]


@router.get("/stats")
def audit_stats(db: Session = Depends(get_db), _: User = Depends(AUDIT_READERS)):
    total = db.execute(select(func.count()).select_from(AuditTrail)).scalar_one()
    by_action = {
        a: c
        for a, c in db.execute(
            select(AuditTrail.action, func.count()).group_by(AuditTrail.action)
        ).all()
    }
    by_entity = {
        e: c
        for e, c in db.execute(
            select(AuditTrail.entity_type, func.count()).group_by(AuditTrail.entity_type)
        ).all()
    }
    by_role = {
        r: c
        for r, c in db.execute(
            select(AuditTrail.user_role, func.count()).group_by(AuditTrail.user_role)
        ).all()
    }
    anchors = db.execute(select(func.count()).select_from(AuditAnchor)).scalar_one()
    return {
        "total_audit_rows": total,
        "total_anchors": anchors,
        "pending_unanchored_rows": len(anchoring.pending_rows(db)),
        "anchor_mode": settings.ANCHOR_MODE,
        "by_action": by_action,
        "by_entity_type": by_entity,
        "by_user_role": by_role,
        "immutability": (
            "audit_trail and audit_anchors are append-only: UPDATE, DELETE and "
            "TRUNCATE are rejected by PostgreSQL triggers."
        ),
    }


@router.get("/chain/verify")
def verify_chain(
    start_id: int | None = None,
    end_id: int | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(RequireRoles("admin", "regulator")),
):
    """Recompute the SHA-256 hash chain from raw PostgreSQL rows."""
    return verify_hash_chain(db, start_id, end_id)


@router.get("/anchors")
def list_anchors(db: Session = Depends(get_db),
                 _: User = Depends(RequireRoles("admin", "regulator", "pi", "ec", "pv"))):
    rows = db.execute(select(AuditAnchor).order_by(AuditAnchor.id.desc())).scalars().all()
    ok, why = anchoring.polygon_available()
    return {
        "anchor_mode": settings.ANCHOR_MODE,
        "polygon_ready": ok,
        "polygon_note": why,
        "batch_size": settings.ANCHOR_BATCH_SIZE,
        "pending_unanchored_rows": len(anchoring.pending_rows(db)),
        "anchors": [anchoring.anchor_to_dict(a) for a in rows],
    }


@router.post("/anchors/commit")
def commit_anchor(
    force: bool = True,
    ctx: AuditContext = Depends(audit_ctx),
    _: User = Depends(RequireRoles("admin")),
):
    """Batch the un-anchored audit rows, compute the Merkle root and anchor it."""
    result = anchoring.commit_batch(ctx.db, force=force)
    if result["anchored"]:
        ctx.log("ANCHOR_COMMIT", "AuditAnchor", result["anchor"]["id"],
                new=result["anchor"], reason=result["reason"])
    return result


@router.get("/verify")
def verify(db: Session = Depends(get_db),
           _: User = Depends(RequireRoles("admin", "regulator", "pi", "ec", "pv", "monitor"))):
    """THE tamper-evidence endpoint.

    Recomputes every anchored batch's Merkle root from the live PostgreSQL rows
    and compares it against the anchored root (and the on-chain root when
    POLYGON_AMOY anchoring is enabled). Any silent edit is exposed here.
    """
    return anchoring.verify_anchors(db)


@router.get("/proof/{audit_id}")
def inclusion_proof(audit_id: int, db: Session = Depends(get_db),
                    _: User = Depends(RequireRoles("admin", "regulator", "pi", "ec", "pv"))):
    """Merkle inclusion proof that a specific audit row is part of an anchor."""
    try:
        return anchoring.inclusion_proof(db, audit_id)
    except anchoring.AnchorError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/simulate-tamper")
def simulate_tamper(
    payload: TamperSimRequest,
    ctx: AuditContext = Depends(audit_ctx),
    _: User = Depends(RequireRoles("admin")),
):
    """DEMO ONLY: temporarily drop the immutability triggers and rewrite one
    audit row, as a malicious database administrator would.

    The point of the demo is that /api/audit/verify still detects it, because
    the Merkle leaves are recomputed from the live row content.
    Disable in production with ALLOW_TAMPER_SIM=false.
    """
    if not settings.ALLOW_TAMPER_SIM:
        raise HTTPException(
            status_code=403,
            detail="Tamper simulation is disabled (ALLOW_TAMPER_SIM=false).",
        )
    db = ctx.db
    row = db.get(AuditTrail, payload.audit_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Audit row {payload.audit_id} not found")
    original_action = row.action

    set_audit_triggers(False)
    try:
        db.execute(
            text("UPDATE audit_trail SET action = :a WHERE id = :i"),
            {"a": payload.new_action, "i": payload.audit_id},
        )
        db.commit()
    finally:
        set_audit_triggers(True)
    db.expire_all()

    return {
        "tampered": True,
        "audit_id": payload.audit_id,
        "original_action": original_action,
        "new_action": payload.new_action,
        "restore_hint": (
            f"POST /api/audit/restore-tamper with audit_id={payload.audit_id} and "
            f"original_action='{original_action}'"
        ),
        "verification": anchoring.verify_anchors(db),
    }


@router.post("/restore-tamper")
def restore_tamper(
    audit_id: int,
    original_action: str,
    db: Session = Depends(get_db),
    _: User = Depends(RequireRoles("admin")),
):
    """DEMO ONLY: put the original value back so the chain verifies again."""
    if not settings.ALLOW_TAMPER_SIM:
        raise HTTPException(status_code=403, detail="Tamper simulation is disabled.")
    set_audit_triggers(False)
    try:
        db.execute(
            text("UPDATE audit_trail SET action = :a WHERE id = :i"),
            {"a": original_action, "i": audit_id},
        )
        db.commit()
    finally:
        set_audit_triggers(True)
    db.expire_all()
    return {"restored": True, "audit_id": audit_id, "verification": anchoring.verify_anchors(db)}


@router.get("/immutability-test")
def immutability_test(db: Session = Depends(get_db), _: User = Depends(AUDIT_READERS)):
    """Live proof that the database rejects UPDATE and DELETE on audit_trail."""
    row_id = db.execute(text("SELECT id FROM audit_trail ORDER BY id LIMIT 1")).scalar()
    if row_id is None:
        raise HTTPException(status_code=404, detail="The audit trail is empty")
    results = {}
    for label, sql in (
        ("update", "UPDATE audit_trail SET action = 'TEST' WHERE id = :i"),
        ("delete", "DELETE FROM audit_trail WHERE id = :i"),
    ):
        try:
            db.execute(text(sql), {"i": row_id})
            db.commit()
            results[label] = {"blocked": False, "error": None}
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            results[label] = {"blocked": True, "error": str(exc).split("\n")[0]}
    return {
        "tested_audit_id": row_id,
        "results": results,
        "conclusion": (
            "APPEND-ONLY ENFORCED"
            if all(r["blocked"] for r in results.values())
            else "WARNING: immutability triggers are not active"
        ),
    }
