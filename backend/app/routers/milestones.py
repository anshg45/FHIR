"""Milestone CRUD (study-independent endpoints)."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import AuditContext, audit_ctx
from ..models import Milestone, User
from ..rules import accessible_study_ids
from ..schemas import MilestoneOut, MilestoneUpdate, Paged
from ..security import RequireRoles, get_current_user

router = APIRouter(prefix="/milestones", tags=["Milestones"])


@router.get("", response_model=Paged)
def list_milestones(
    study_id: str | None = None,
    status: str | None = None,
    owner_role: str | None = None,
    overdue_only: bool = False,
    limit: int = Query(100, le=500),
    offset: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = select(Milestone)
    scoped = accessible_study_ids(db, user)
    if scoped is not None:
        if not scoped:
            return Paged(total=0, limit=limit, offset=offset, items=[])
        stmt = stmt.where(Milestone.study_id.in_(scoped))
    if study_id:
        stmt = stmt.where(Milestone.study_id == study_id)
    if status:
        stmt = stmt.where(Milestone.status == status)
    if owner_role:
        stmt = stmt.where(Milestone.owner_role == owner_role)
    if overdue_only:
        stmt = stmt.where(
            Milestone.status != "completed",
            Milestone.due_date.isnot(None),
            Milestone.due_date < date.today(),
        )
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = (
        db.execute(stmt.order_by(Milestone.due_date).limit(limit).offset(offset)).scalars().all()
    )
    return Paged(
        total=total, limit=limit, offset=offset,
        items=[MilestoneOut.model_validate(m).model_dump() for m in rows],
    )


@router.put("/{milestone_id}", response_model=MilestoneOut)
def update_milestone(
    milestone_id: str,
    payload: MilestoneUpdate,
    ctx: AuditContext = Depends(audit_ctx),
    _: User = Depends(RequireRoles("admin", "pi", "ec", "coordinator")),
):
    db = ctx.db
    m = db.get(Milestone, milestone_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Milestone not found")
    before = MilestoneOut.model_validate(m).model_dump()
    for f, v in payload.model_dump(exclude_unset=True).items():
        setattr(m, f, v)
    db.commit()
    db.refresh(m)
    ctx.log("UPDATE", "Milestone", m.id, old=before,
            new=MilestoneOut.model_validate(m).model_dump())
    return MilestoneOut.model_validate(m)


@router.post("/{milestone_id}/complete", response_model=MilestoneOut)
def complete_milestone(
    milestone_id: str,
    completed_date: date | None = None,
    ctx: AuditContext = Depends(audit_ctx),
    _: User = Depends(RequireRoles("admin", "pi", "ec", "coordinator")),
):
    db = ctx.db
    m = db.get(Milestone, milestone_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Milestone not found")
    old = {"status": m.status, "completed_date": m.completed_date}
    m.status = "completed"
    m.completed_date = completed_date or date.today()
    db.commit()
    db.refresh(m)
    ctx.log("COMPLETE", "Milestone", m.id, old=old,
            new={"status": m.status, "completed_date": m.completed_date})
    return MilestoneOut.model_validate(m)


@router.delete("/{milestone_id}")
def delete_milestone(milestone_id: str, ctx: AuditContext = Depends(audit_ctx),
                     _: User = Depends(RequireRoles("admin"))):
    db = ctx.db
    m = db.get(Milestone, milestone_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Milestone not found")
    old = MilestoneOut.model_validate(m).model_dump()
    db.delete(m)
    db.commit()
    ctx.log("DELETE", "Milestone", milestone_id, old=old)
    return {"success": True}
