"""System-wide alert inbox + threshold administration."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import AuditContext, audit_ctx
from ..jobs import DEFAULT_THRESHOLDS, ensure_default_thresholds, run_all_jobs
from ..models import Alert, AlertThreshold, User
from ..schemas import AlertOut, Paged, ThresholdOut, ThresholdUpsert
from ..security import RequireRoles, get_current_user

router = APIRouter(prefix="/alerts", tags=["Alerts & Thresholds"])


@router.get("", response_model=Paged)
def list_alerts(
    study_id: str | None = None,
    alert_type: str | None = None,
    severity: str | None = None,
    resolved: bool | None = None,
    is_read: bool | None = None,
    limit: int = Query(100, le=500),
    offset: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = select(Alert)
    if study_id:
        stmt = stmt.where(Alert.study_id == study_id)
    if alert_type:
        stmt = stmt.where(Alert.alert_type == alert_type)
    if severity:
        stmt = stmt.where(Alert.severity == severity)
    if resolved is not None:
        stmt = stmt.where(Alert.resolved.is_(resolved))
    if is_read is not None:
        stmt = stmt.where(Alert.is_read.is_(is_read))
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = (
        db.execute(stmt.order_by(Alert.created_at.desc()).limit(limit).offset(offset))
        .scalars().all()
    )
    return Paged(total=total, limit=limit, offset=offset,
                 items=[AlertOut.model_validate(a).model_dump() for a in rows])


@router.post("/{alert_id}/read", response_model=AlertOut)
def mark_read(alert_id: str, db: Session = Depends(get_db),
              _: User = Depends(get_current_user)):
    a = db.get(Alert, alert_id)
    if a is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    a.is_read = True
    db.commit()
    db.refresh(a)
    return AlertOut.model_validate(a)


@router.post("/{alert_id}/resolve", response_model=AlertOut)
def resolve_alert(alert_id: str, ctx: AuditContext = Depends(audit_ctx),
                  _: User = Depends(RequireRoles("admin", "pi", "monitor", "ec", "pv"))):
    db = ctx.db
    a = db.get(Alert, alert_id)
    if a is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    a.resolved = True
    a.is_read = True
    db.commit()
    db.refresh(a)
    ctx.log("RESOLVE_ALERT", "Alert", a.id, new={"resolved": True})
    return AlertOut.model_validate(a)


@router.post("/run-jobs")
def run_jobs(_: User = Depends(RequireRoles("admin"))):
    """Manually trigger every scheduled job (overdue detection, deadlines, anchoring)."""
    return run_all_jobs()


# ---------------------------------------------------------------- thresholds
@router.get("/thresholds", response_model=list[ThresholdOut])
def list_thresholds(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    ensure_default_thresholds()
    rows = db.execute(select(AlertThreshold).order_by(AlertThreshold.key)).scalars().all()
    return [ThresholdOut.model_validate(r) for r in rows]


@router.put("/thresholds", response_model=ThresholdOut)
def upsert_threshold(payload: ThresholdUpsert,
                     ctx: AuditContext = Depends(audit_ctx),
                     _: User = Depends(RequireRoles("admin"))):
    db = ctx.db
    t = db.execute(
        select(AlertThreshold).where(AlertThreshold.key == payload.key)
    ).scalars().first()
    old = None
    if t is None:
        t = AlertThreshold(key=payload.key, label=payload.label, value=payload.value,
                           unit=payload.unit)
        db.add(t)
    else:
        old = {"value": t.value, "label": t.label, "unit": t.unit}
        t.value = payload.value
        if payload.label is not None:
            t.label = payload.label
        if payload.unit is not None:
            t.unit = payload.unit
    db.commit()
    db.refresh(t)
    ctx.log("UPSERT_THRESHOLD", "AlertThreshold", t.id, old=old,
            new={"key": t.key, "value": t.value})
    return ThresholdOut.model_validate(t)
