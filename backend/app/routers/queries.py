"""Data query management (monitor raises, coordinator answers, monitor closes)."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import AuditContext, audit_ctx, get_scoped_study
from ..models import DataQuery, User
from ..rules import accessible_study_ids, assert_not_locked
from ..schemas import Paged, QueryAnswer, QueryCreate, QueryOut
from ..security import RequireRoles, get_current_user

router = APIRouter(prefix="/queries", tags=["Data Queries"])


@router.get("", response_model=Paged)
def list_queries(
    study_id: str | None = None,
    patient_id: str | None = None,
    status: str | None = None,
    priority: str | None = None,
    limit: int = Query(100, le=500),
    offset: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = select(DataQuery)
    scoped = accessible_study_ids(db, user)
    if scoped is not None:
        if not scoped:
            return Paged(total=0, limit=limit, offset=offset, items=[])
        stmt = stmt.where(DataQuery.study_id.in_(scoped))
    if study_id:
        stmt = stmt.where(DataQuery.study_id == study_id)
    if patient_id:
        stmt = stmt.where(DataQuery.patient_id == patient_id)
    if status:
        stmt = stmt.where(DataQuery.status == status)
    if priority:
        stmt = stmt.where(DataQuery.priority == priority)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = (
        db.execute(stmt.order_by(DataQuery.raised_date.desc()).limit(limit).offset(offset))
        .scalars()
        .all()
    )
    return Paged(
        total=total, limit=limit, offset=offset,
        items=[QueryOut.model_validate(q).model_dump() for q in rows],
    )


@router.get("/inbox", response_model=Paged)
def my_inbox(
    limit: int = Query(100, le=500),
    offset: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Role-aware query inbox.

    Coordinators see queries awaiting an answer; monitors see the ones they
    raised plus everything answered and awaiting closure.
    """
    stmt = select(DataQuery)
    scoped = accessible_study_ids(db, user)
    if scoped is not None:
        if not scoped:
            return Paged(total=0, limit=limit, offset=offset, items=[])
        stmt = stmt.where(DataQuery.study_id.in_(scoped))
    if user.role == "coordinator":
        stmt = stmt.where(DataQuery.status == "open")
    elif user.role == "monitor":
        stmt = stmt.where(
            or_(DataQuery.raised_by == user.id, DataQuery.status == "answered")
        )
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = (
        db.execute(stmt.order_by(DataQuery.raised_date.desc()).limit(limit).offset(offset))
        .scalars()
        .all()
    )
    return Paged(
        total=total, limit=limit, offset=offset,
        items=[QueryOut.model_validate(q).model_dump() for q in rows],
    )


@router.post("", response_model=QueryOut, status_code=201)
def raise_query(
    payload: QueryCreate,
    ctx: AuditContext = Depends(audit_ctx),
    user: User = Depends(RequireRoles("monitor", "pi", "admin", "pv")),
):
    db = ctx.db
    study = get_scoped_study(db, user, payload.study_id)
    assert_not_locked(study)
    q = DataQuery(**payload.model_dump(), raised_by=user.id, status="open",
                  raised_date=datetime.now(timezone.utc))
    db.add(q)
    db.commit()
    db.refresh(q)
    ctx.log("RAISE_QUERY", "DataQuery", q.id, new=q, reason=payload.query_text[:200])
    return QueryOut.model_validate(q)


@router.get("/{query_id}", response_model=QueryOut)
def get_query(query_id: str, db: Session = Depends(get_db),
              user: User = Depends(get_current_user)):
    q = db.get(DataQuery, query_id)
    if q is None:
        raise HTTPException(status_code=404, detail="Query not found")
    get_scoped_study(db, user, q.study_id)
    return QueryOut.model_validate(q)


@router.post("/{query_id}/answer", response_model=QueryOut)
def answer_query(
    query_id: str,
    payload: QueryAnswer,
    ctx: AuditContext = Depends(audit_ctx),
    user: User = Depends(RequireRoles("coordinator", "pi", "admin")),
):
    db = ctx.db
    q = db.get(DataQuery, query_id)
    if q is None:
        raise HTTPException(status_code=404, detail="Query not found")
    if q.status == "closed":
        raise HTTPException(status_code=409, detail="Query is already closed")
    before = QueryOut.model_validate(q).model_dump()
    q.response_text = payload.response_text
    q.responded_by = user.id
    q.responded_date = datetime.now(timezone.utc)
    q.status = "answered"
    db.commit()
    db.refresh(q)
    ctx.log("ANSWER_QUERY", "DataQuery", q.id, old=before,
            new=QueryOut.model_validate(q).model_dump(), reason=payload.response_text[:200])
    return QueryOut.model_validate(q)


@router.post("/{query_id}/close", response_model=QueryOut)
def close_query(
    query_id: str,
    ctx: AuditContext = Depends(audit_ctx),
    user: User = Depends(RequireRoles("monitor", "pi", "admin")),
):
    db = ctx.db
    q = db.get(DataQuery, query_id)
    if q is None:
        raise HTTPException(status_code=404, detail="Query not found")
    if q.status != "answered":
        raise HTTPException(
            status_code=409,
            detail=f"Only answered queries can be closed (current status: '{q.status}')",
        )
    before = QueryOut.model_validate(q).model_dump()
    q.status = "closed"
    q.closed_by = user.id
    q.closed_date = datetime.now(timezone.utc)
    db.commit()
    db.refresh(q)
    ctx.log("CLOSE_QUERY", "DataQuery", q.id, old=before,
            new=QueryOut.model_validate(q).model_dump())
    return QueryOut.model_validate(q)
