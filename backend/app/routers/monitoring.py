"""Monitoring visit reports (Monitor role)."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import AuditContext, audit_ctx, get_scoped_study
from ..models import MonitoringVisitReport, User
from ..rules import accessible_study_ids
from ..schemas import (
    MonitoringReportCreate,
    MonitoringReportOut,
    MonitoringReportUpdate,
    Paged,
)
from ..security import RequireRoles, get_current_user

router = APIRouter(prefix="/monitoring-reports", tags=["Monitoring"])


@router.get("", response_model=Paged)
def list_reports(
    study_id: str | None = None,
    status: str | None = None,
    limit: int = Query(100, le=500),
    offset: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = select(MonitoringVisitReport)
    scoped = accessible_study_ids(db, user)
    if scoped is not None:
        if not scoped:
            return Paged(total=0, limit=limit, offset=offset, items=[])
        stmt = stmt.where(MonitoringVisitReport.study_id.in_(scoped))
    if study_id:
        stmt = stmt.where(MonitoringVisitReport.study_id == study_id)
    if status:
        stmt = stmt.where(MonitoringVisitReport.status == status)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = (
        db.execute(
            stmt.order_by(MonitoringVisitReport.visit_date.desc()).limit(limit).offset(offset)
        )
        .scalars()
        .all()
    )
    return Paged(
        total=total, limit=limit, offset=offset,
        items=[MonitoringReportOut.model_validate(r).model_dump() for r in rows],
    )


@router.post("", response_model=MonitoringReportOut, status_code=201)
def create_report(
    payload: MonitoringReportCreate,
    ctx: AuditContext = Depends(audit_ctx),
    user: User = Depends(RequireRoles("monitor", "admin")),
):
    db = ctx.db
    study = get_scoped_study(db, user, payload.study_id)
    r = MonitoringVisitReport(**payload.model_dump(), monitor_id=user.id, status="draft")
    r.study_id = study.id
    db.add(r)
    db.commit()
    db.refresh(r)
    ctx.log("CREATE", "MonitoringVisitReport", r.id, new=r)
    return MonitoringReportOut.model_validate(r)


@router.get("/{report_id}", response_model=MonitoringReportOut)
def get_report(report_id: str, db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    r = db.get(MonitoringVisitReport, report_id)
    if r is None:
        raise HTTPException(status_code=404, detail="Monitoring report not found")
    get_scoped_study(db, user, r.study_id)
    return MonitoringReportOut.model_validate(r)


@router.put("/{report_id}", response_model=MonitoringReportOut)
def update_report(
    report_id: str,
    payload: MonitoringReportUpdate,
    ctx: AuditContext = Depends(audit_ctx),
    _: User = Depends(RequireRoles("monitor", "admin")),
):
    db = ctx.db
    r = db.get(MonitoringVisitReport, report_id)
    if r is None:
        raise HTTPException(status_code=404, detail="Monitoring report not found")
    before = MonitoringReportOut.model_validate(r).model_dump()
    for f, v in payload.model_dump(exclude_unset=True).items():
        setattr(r, f, v)
    db.commit()
    db.refresh(r)
    ctx.log("UPDATE", "MonitoringVisitReport", r.id, old=before,
            new=MonitoringReportOut.model_validate(r).model_dump())
    return MonitoringReportOut.model_validate(r)


@router.post("/{report_id}/submit", response_model=MonitoringReportOut)
def submit_report(report_id: str, ctx: AuditContext = Depends(audit_ctx),
                  _: User = Depends(RequireRoles("monitor", "admin"))):
    db = ctx.db
    r = db.get(MonitoringVisitReport, report_id)
    if r is None:
        raise HTTPException(status_code=404, detail="Monitoring report not found")
    old = {"status": r.status}
    r.status = "submitted"
    db.commit()
    db.refresh(r)
    ctx.log("SUBMIT", "MonitoringVisitReport", r.id, old=old, new={"status": r.status})
    return MonitoringReportOut.model_validate(r)
