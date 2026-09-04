"""Protocol deviations."""
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import AuditContext, audit_ctx, get_scoped_study
from ..models import ProtocolDeviation, User
from ..rules import accessible_study_ids, assert_not_locked
from ..schemas import DeviationCreate, DeviationOut, DeviationReview, Paged
from ..security import RequireRoles, get_current_user

router = APIRouter(prefix="/deviations", tags=["Protocol Deviations"])


@router.get("", response_model=Paged)
def list_deviations(
    study_id: str | None = None,
    patient_id: str | None = None,
    severity: str | None = None,
    status: str | None = None,
    limit: int = Query(100, le=500),
    offset: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = select(ProtocolDeviation)
    scoped = accessible_study_ids(db, user)
    if scoped is not None:
        if not scoped:
            return Paged(total=0, limit=limit, offset=offset, items=[])
        stmt = stmt.where(ProtocolDeviation.study_id.in_(scoped))
    if study_id:
        stmt = stmt.where(ProtocolDeviation.study_id == study_id)
    if patient_id:
        stmt = stmt.where(ProtocolDeviation.patient_id == patient_id)
    if severity:
        stmt = stmt.where(ProtocolDeviation.severity == severity)
    if status:
        stmt = stmt.where(ProtocolDeviation.status == status)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = (
        db.execute(
            stmt.order_by(ProtocolDeviation.reported_date.desc()).limit(limit).offset(offset)
        )
        .scalars()
        .all()
    )
    return Paged(
        total=total, limit=limit, offset=offset,
        items=[DeviationOut.model_validate(d).model_dump() for d in rows],
    )


@router.post("", response_model=DeviationOut, status_code=201)
def create_deviation(
    payload: DeviationCreate,
    ctx: AuditContext = Depends(audit_ctx),
    user: User = Depends(RequireRoles("coordinator", "monitor", "pi", "admin")),
):
    db = ctx.db
    study = get_scoped_study(db, user, payload.study_id)
    assert_not_locked(study)
    seq = db.execute(
        select(func.count())
        .select_from(ProtocolDeviation)
        .where(ProtocolDeviation.study_id == study.id)
    ).scalar_one()
    d = ProtocolDeviation(
        **payload.model_dump(),
        deviation_number=f"{study.protocol_number}-PD{seq + 1:04d}",
        reported_by=user.id,
        status="open",
    )
    if d.reported_date is None:
        d.reported_date = date.today()
    db.add(d)
    db.commit()
    db.refresh(d)
    ctx.log("CREATE", "ProtocolDeviation", d.id, new=d,
            reason=f"{d.severity} deviation reported")
    return DeviationOut.model_validate(d)


@router.get("/{deviation_id}", response_model=DeviationOut)
def get_deviation(deviation_id: str, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    d = db.get(ProtocolDeviation, deviation_id)
    if d is None:
        raise HTTPException(status_code=404, detail="Deviation not found")
    get_scoped_study(db, user, d.study_id)
    return DeviationOut.model_validate(d)


@router.post("/{deviation_id}/review", response_model=DeviationOut)
def review_deviation(
    deviation_id: str,
    payload: DeviationReview,
    ctx: AuditContext = Depends(audit_ctx),
    user: User = Depends(RequireRoles("monitor", "pi", "admin", "ec")),
):
    db = ctx.db
    d = db.get(ProtocolDeviation, deviation_id)
    if d is None:
        raise HTTPException(status_code=404, detail="Deviation not found")
    before = DeviationOut.model_validate(d).model_dump()
    if payload.resolution is not None:
        d.resolution = payload.resolution
    if payload.corrective_action is not None:
        d.corrective_action = payload.corrective_action
    d.status = payload.status
    d.reviewed_by = user.id
    d.reviewed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(d)
    ctx.log("REVIEW", "ProtocolDeviation", d.id, old=before,
            new=DeviationOut.model_validate(d).model_dump(),
            reason=payload.resolution)
    return DeviationOut.model_validate(d)


@router.get("/stats/by-severity")
def stats_by_severity(study_id: str | None = None, db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    stmt = select(ProtocolDeviation.severity, func.count()).group_by(
        ProtocolDeviation.severity
    )
    scoped = accessible_study_ids(db, user)
    if scoped is not None:
        if not scoped:
            return {"by_severity": {}, "total": 0}
        stmt = stmt.where(ProtocolDeviation.study_id.in_(scoped))
    if study_id:
        stmt = stmt.where(ProtocolDeviation.study_id == study_id)
    data = {s: c for s, c in db.execute(stmt).all()}
    return {"by_severity": data, "total": sum(data.values())}
