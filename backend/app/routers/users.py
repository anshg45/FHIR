"""User management (admin only) + study assignments."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import AuditContext, audit_ctx
from ..enums import ALL_ROLES
from ..models import StudyAssignment, User
from ..schemas import Paged, UserCreate, UserOut, UserUpdate
from ..security import RequireRoles, get_current_user, hash_password

router = APIRouter(prefix="/users", tags=["Users & Roles"])
admin_only = RequireRoles("admin")


@router.get("/roles")
def list_roles():
    return {
        "roles": [
            {"code": "pi", "label": "Principal Investigator",
             "description": "Owns studies, submits protocols, signs close-out"},
            {"code": "coordinator", "label": "Study Coordinator",
             "description": "Screening, enrollment, visit data entry, AE logging"},
            {"code": "monitor", "label": "Monitor",
             "description": "Compliance checks, data queries, deviation review"},
            {"code": "ec", "label": "Ethics Committee",
             "description": "Protocol approvals, SAE escalations, IEC milestones"},
            {"code": "pv", "label": "Pharmacovigilance (NPvCC)",
             "description": "AE/SAE inbox, coding, regulatory deadlines, DSMB feed"},
            {"code": "admin", "label": "Administration",
             "description": "Users, sites, thresholds, system-wide audit viewer"},
            {"code": "regulator", "label": "Regulator",
             "description": "Read-only inspection view + audit anchor panel"},
        ]
    }


@router.get("", response_model=Paged)
def list_users(
    role: str | None = None,
    site_id: str | None = None,
    is_active: bool | None = None,
    search: str | None = None,
    limit: int = Query(50, le=500),
    offset: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(RequireRoles("admin", "regulator", "pi")),
):
    stmt = select(User)
    if role:
        if role not in ALL_ROLES:
            raise HTTPException(status_code=400, detail=f"Unknown role '{role}'")
        stmt = stmt.where(User.role == role)
    if site_id:
        stmt = stmt.where(User.site_id == site_id)
    if is_active is not None:
        stmt = stmt.where(User.is_active.is_(is_active))
    if search:
        like = f"%{search.lower()}%"
        stmt = stmt.where(
            func.lower(User.name).like(like) | func.lower(User.email).like(like)
        )
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = (
        db.execute(stmt.order_by(User.name).limit(limit).offset(offset)).scalars().all()
    )
    return Paged(
        total=total, limit=limit, offset=offset,
        items=[UserOut.model_validate(r).model_dump() for r in rows],
    )


@router.post("", response_model=UserOut, status_code=201)
def create_user(
    payload: UserCreate,
    ctx: AuditContext = Depends(audit_ctx),
    _: User = Depends(admin_only),
):
    db = ctx.db
    if db.execute(select(User).where(User.email == payload.email.lower())).scalars().first():
        raise HTTPException(status_code=409, detail="A user with this email already exists")
    user = User(
        name=payload.name,
        email=payload.email.lower(),
        password_hash=hash_password(payload.password),
        role=payload.role,
        designation=payload.designation,
        phone=payload.phone,
        site_id=payload.site_id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    ctx.log("CREATE", "User", user.id, new={"email": user.email, "role": user.role,
                                            "name": user.name})
    return UserOut.model_validate(user)


@router.get("/{user_id}", response_model=UserOut)
def get_user(user_id: str, db: Session = Depends(get_db),
             _: User = Depends(RequireRoles("admin", "regulator"))):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(status_code=404, detail="User not found")
    return UserOut.model_validate(u)


@router.put("/{user_id}", response_model=UserOut)
def update_user(
    user_id: str,
    payload: UserUpdate,
    ctx: AuditContext = Depends(audit_ctx),
    _: User = Depends(admin_only),
):
    db = ctx.db
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(status_code=404, detail="User not found")
    before = {"role": u.role, "is_active": u.is_active, "name": u.name, "site_id": u.site_id}
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(u, field, value)
    db.commit()
    db.refresh(u)
    ctx.log("UPDATE", "User", u.id, old=before,
            new={"role": u.role, "is_active": u.is_active, "name": u.name,
                 "site_id": u.site_id})
    return UserOut.model_validate(u)


@router.post("/{user_id}/deactivate", response_model=UserOut)
def deactivate(user_id: str, ctx: AuditContext = Depends(audit_ctx),
               actor: User = Depends(admin_only)):
    db = ctx.db
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(status_code=404, detail="User not found")
    if u.id == actor.id:
        raise HTTPException(status_code=400, detail="You cannot deactivate your own account")
    u.is_active = False
    db.commit()
    db.refresh(u)
    ctx.log("DEACTIVATE", "User", u.id, old={"is_active": True}, new={"is_active": False})
    return UserOut.model_validate(u)


@router.post("/{user_id}/activate", response_model=UserOut)
def activate(user_id: str, ctx: AuditContext = Depends(audit_ctx),
             _: User = Depends(admin_only)):
    db = ctx.db
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(status_code=404, detail="User not found")
    u.is_active = True
    db.commit()
    db.refresh(u)
    ctx.log("ACTIVATE", "User", u.id, old={"is_active": False}, new={"is_active": True})
    return UserOut.model_validate(u)


@router.post("/{user_id}/reset-password")
def reset_password(user_id: str, new_password: str = Query(min_length=8),
                   ctx: AuditContext = Depends(audit_ctx),
                   _: User = Depends(admin_only)):
    db = ctx.db
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(status_code=404, detail="User not found")
    u.password_hash = hash_password(new_password)
    db.commit()
    ctx.log("RESET_PASSWORD", "User", u.id, new={"password_reset_by_admin": True})
    return {"success": True, "message": f"Password reset for {u.email}"}


@router.get("/{user_id}/studies")
def user_studies(user_id: str, db: Session = Depends(get_db),
                 _: User = Depends(get_current_user)):
    rows = (
        db.execute(select(StudyAssignment).where(StudyAssignment.user_id == user_id))
        .scalars()
        .all()
    )
    return {
        "user_id": user_id,
        "assignments": [
            {"study_id": r.study_id, "role_on_study": r.role_on_study} for r in rows
        ],
    }
