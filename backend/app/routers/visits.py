"""Visit scheduling, completion and compliance tracking."""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import AuditContext, audit_ctx, get_scoped_study
from ..jobs import refresh_overdue_visits
from ..models import Patient, ProtocolDeviation, User, VisitLog
from ..rules import accessible_study_ids, assert_not_locked, assert_study_access
from ..schemas import (
    Paged,
    VisitComplete,
    VisitCreate,
    VisitOut,
    VisitScheduleBulk,
    VisitUpdate,
)
from ..security import RequireRoles, get_current_user

router = APIRouter(prefix="/visits", tags=["Visits & Compliance"])
WRITE_ROLES = RequireRoles("coordinator", "pi", "admin")


def _get_visit(db: Session, user: User, visit_id: str) -> VisitLog:
    v = db.get(VisitLog, visit_id)
    if v is None:
        raise HTTPException(status_code=404, detail="Visit not found")
    assert_study_access(db, user, v.patient.study)
    return v


@router.get("", response_model=Paged)
def list_visits(
    study_id: str | None = None,
    patient_id: str | None = None,
    status: str | None = None,
    deviation_only: bool = False,
    limit: int = Query(200, le=1000),
    offset: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = select(VisitLog)
    scoped = accessible_study_ids(db, user)
    if scoped is not None:
        if not scoped:
            return Paged(total=0, limit=limit, offset=offset, items=[])
        stmt = stmt.where(VisitLog.study_id.in_(scoped))
    if study_id:
        stmt = stmt.where(VisitLog.study_id == study_id)
    if patient_id:
        stmt = stmt.where(VisitLog.patient_id == patient_id)
    if status:
        stmt = stmt.where(VisitLog.status == status)
    if deviation_only:
        stmt = stmt.where(VisitLog.deviation_flag.is_(True))
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = (
        db.execute(
            stmt.order_by(VisitLog.scheduled_date, VisitLog.visit_number)
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return Paged(
        total=total, limit=limit, offset=offset,
        items=[VisitOut.model_validate(v).model_dump() for v in rows],
    )


@router.post("", response_model=VisitOut, status_code=201)
def create_visit(payload: VisitCreate, ctx: AuditContext = Depends(audit_ctx),
                 user: User = Depends(WRITE_ROLES)):
    db = ctx.db
    study = get_scoped_study(db, user, payload.study_id)
    assert_not_locked(study)
    p = db.get(Patient, payload.patient_id)
    if p is None or p.study_id != study.id:
        raise HTTPException(status_code=400, detail="patient_id does not belong to this study")
    v = VisitLog(**payload.model_dump())
    db.add(v)
    db.commit()
    db.refresh(v)
    ctx.log("CREATE", "VisitLog", v.id, new=v)
    return VisitOut.model_validate(v)


@router.post("/schedule", response_model=list[VisitOut], status_code=201)
def schedule_visits(payload: VisitScheduleBulk, ctx: AuditContext = Depends(audit_ctx),
                    user: User = Depends(WRITE_ROLES)):
    """Generate the whole protocol visit schedule for one subject."""
    db = ctx.db
    p = db.get(Patient, payload.patient_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    assert_study_access(db, user, p.study)
    assert_not_locked(p.study)
    existing = db.execute(
        select(func.coalesce(func.max(VisitLog.visit_number), 0)).where(
            VisitLog.patient_id == p.id
        )
    ).scalar_one()
    start = payload.first_visit_date or p.enrollment_date or date.today()
    created = []
    for i, name in enumerate(payload.visit_names):
        v = VisitLog(
            study_id=p.study_id,
            patient_id=p.id,
            visit_number=existing + i + 1,
            visit_name=name,
            scheduled_date=start + timedelta(days=payload.interval_days * i),
            status="scheduled",
        )
        db.add(v)
        created.append(v)
    db.commit()
    for v in created:
        db.refresh(v)
    ctx.log("SCHEDULE_VISITS", "Patient", p.id,
            new={"visits_created": len(created), "names": payload.visit_names})
    return [VisitOut.model_validate(v) for v in created]


@router.get("/{visit_id}", response_model=VisitOut)
def get_visit(visit_id: str, db: Session = Depends(get_db),
              user: User = Depends(get_current_user)):
    return VisitOut.model_validate(_get_visit(db, user, visit_id))


@router.put("/{visit_id}", response_model=VisitOut)
def update_visit(visit_id: str, payload: VisitUpdate,
                 ctx: AuditContext = Depends(audit_ctx),
                 user: User = Depends(WRITE_ROLES)):
    db = ctx.db
    v = _get_visit(db, user, visit_id)
    assert_not_locked(v.patient.study)
    before = VisitOut.model_validate(v).model_dump()
    for f, val in payload.model_dump(exclude_unset=True).items():
        setattr(v, f, val)
    db.commit()
    db.refresh(v)
    ctx.log("UPDATE", "VisitLog", v.id, old=before,
            new=VisitOut.model_validate(v).model_dump())
    return VisitOut.model_validate(v)


@router.post("/{visit_id}/complete", response_model=VisitOut)
def complete_visit(
    visit_id: str,
    payload: VisitComplete,
    auto_log_deviation: bool = True,
    ctx: AuditContext = Depends(audit_ctx),
    user: User = Depends(WRITE_ROLES),
):
    """Complete a visit. Out-of-window visits automatically raise a deviation."""
    db = ctx.db
    v = _get_visit(db, user, visit_id)
    assert_not_locked(v.patient.study)
    before = VisitOut.model_validate(v).model_dump()
    actual = payload.actual_date or date.today()
    v.actual_date = actual
    v.status = "completed"
    v.performed_by = user.id
    v.notes = payload.notes

    deviation_created = None
    if v.scheduled_date:
        drift = abs((actual - v.scheduled_date).days)
        if drift > (v.window_days or 0):
            v.deviation_flag = True
            if auto_log_deviation:
                dev = ProtocolDeviation(
                    study_id=v.study_id,
                    patient_id=v.patient_id,
                    visit_log_id=v.id,
                    description=(
                        f"Visit {v.visit_number} ({v.visit_name or 'unnamed'}) performed on "
                        f"{actual} against a scheduled date of {v.scheduled_date} "
                        f"(window +/-{v.window_days} days, drift {drift} days)."
                    ),
                    category="Visit window deviation",
                    severity="major" if drift > (v.window_days or 0) * 3 else "minor",
                    reported_by=user.id,
                    reported_date=date.today(),
                    status="open",
                )
                db.add(dev)
                db.flush()
                deviation_created = dev.id
    db.commit()
    db.refresh(v)
    ctx.log("COMPLETE_VISIT", "VisitLog", v.id, old=before,
            new=VisitOut.model_validate(v).model_dump(),
            reason=(f"Auto-logged deviation {deviation_created}" if deviation_created else None))
    out = VisitOut.model_validate(v).model_dump()
    out["auto_deviation_id"] = deviation_created
    return out


@router.post("/{visit_id}/miss", response_model=VisitOut)
def miss_visit(visit_id: str, reason: str = Query(min_length=3),
               ctx: AuditContext = Depends(audit_ctx),
               user: User = Depends(WRITE_ROLES)):
    db = ctx.db
    v = _get_visit(db, user, visit_id)
    before = {"status": v.status}
    v.status = "missed"
    v.deviation_flag = True
    v.notes = reason
    dev = ProtocolDeviation(
        study_id=v.study_id,
        patient_id=v.patient_id,
        visit_log_id=v.id,
        description=f"Visit {v.visit_number} missed. Reason: {reason}",
        category="Missed visit",
        severity="major",
        reported_by=user.id,
        reported_date=date.today(),
        status="open",
    )
    db.add(dev)
    db.commit()
    db.refresh(v)
    ctx.log("MISS_VISIT", "VisitLog", v.id, old=before,
            new={"status": "missed", "reason": reason}, reason=reason)
    return VisitOut.model_validate(v)


@router.post("/refresh-overdue")
def refresh_overdue(_: User = Depends(RequireRoles("admin", "monitor", "coordinator", "pi"))):
    """Run the overdue-visit detection job on demand."""
    return refresh_overdue_visits()


@router.get("/compliance/summary")
def compliance_summary(study_id: str | None = None, db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    stmt = select(VisitLog.status, func.count()).group_by(VisitLog.status)
    scoped = accessible_study_ids(db, user)
    if scoped is not None:
        if not scoped:
            return {"total": 0, "by_status": {}, "compliance_rate": 0.0}
        stmt = stmt.where(VisitLog.study_id.in_(scoped))
    if study_id:
        stmt = stmt.where(VisitLog.study_id == study_id)
    by_status = {s: c for s, c in db.execute(stmt).all()}
    total = sum(by_status.values())
    completed = by_status.get("completed", 0)
    return {
        "total": total,
        "by_status": by_status,
        "compliance_rate": round(completed / total * 100, 1) if total else 0.0,
        "missed_rate": round(by_status.get("missed", 0) / total * 100, 1) if total else 0.0,
        "overdue_rate": round(by_status.get("overdue", 0) / total * 100, 1) if total else 0.0,
    }
