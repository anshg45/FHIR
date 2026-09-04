"""Trial site management."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import AuditContext, audit_ctx
from ..models import Patient, ResearchStudy, Site, User
from ..schemas import SiteCreate, SiteOut, SiteUpdate
from ..security import RequireRoles, get_current_user

router = APIRouter(prefix="/sites", tags=["Sites"])
admin_only = RequireRoles("admin")


@router.get("", response_model=list[SiteOut])
def list_sites(include_inactive: bool = False, db: Session = Depends(get_db),
               _: User = Depends(get_current_user)):
    stmt = select(Site)
    if not include_inactive:
        stmt = stmt.where(Site.is_active.is_(True))
    return [SiteOut.model_validate(s) for s in db.execute(stmt.order_by(Site.name)).scalars()]


@router.post("", response_model=SiteOut, status_code=201)
def create_site(payload: SiteCreate, ctx: AuditContext = Depends(audit_ctx),
                _: User = Depends(admin_only)):
    db = ctx.db
    if db.execute(select(Site).where(Site.code == payload.code)).scalars().first():
        raise HTTPException(status_code=409, detail="Site code already exists")
    site = Site(**payload.model_dump())
    db.add(site)
    db.commit()
    db.refresh(site)
    ctx.log("CREATE", "Site", site.id, new={"code": site.code, "name": site.name})
    return SiteOut.model_validate(site)


@router.get("/{site_id}", response_model=SiteOut)
def get_site(site_id: str, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    s = db.get(Site, site_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Site not found")
    return SiteOut.model_validate(s)


@router.put("/{site_id}", response_model=SiteOut)
def update_site(site_id: str, payload: SiteUpdate, ctx: AuditContext = Depends(audit_ctx),
                _: User = Depends(admin_only)):
    db = ctx.db
    s = db.get(Site, site_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Site not found")
    before = {"name": s.name, "is_active": s.is_active}
    for f, v in payload.model_dump(exclude_unset=True).items():
        setattr(s, f, v)
    db.commit()
    db.refresh(s)
    ctx.log("UPDATE", "Site", s.id, old=before, new={"name": s.name, "is_active": s.is_active})
    return SiteOut.model_validate(s)


@router.get("/{site_id}/summary")
def site_summary(site_id: str, db: Session = Depends(get_db),
                 _: User = Depends(get_current_user)):
    s = db.get(Site, site_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Site not found")
    studies = db.execute(
        select(func.count()).select_from(ResearchStudy).where(ResearchStudy.site_id == site_id)
    ).scalar_one()
    patients = db.execute(
        select(func.count()).select_from(Patient).where(Patient.site_id == site_id)
    ).scalar_one()
    users = db.execute(
        select(func.count()).select_from(User).where(User.site_id == site_id)
    ).scalar_one()
    return {
        "site": SiteOut.model_validate(s).model_dump(),
        "studies": studies,
        "patients": patients,
        "users": users,
    }
