"""Shared FastAPI dependencies / helpers."""
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .audit import log_action
from .database import get_db
from .models import ResearchStudy, User
from .rules import assert_study_access
from .security import client_ip, get_current_user


class AuditContext:
    """Captures request metadata so every audit row carries who / from where."""

    def __init__(self, request: Request, user: User, db: Session):
        self.request = request
        self.user = user
        self.db = db
        self.ip = client_ip(request)
        self.ua = request.headers.get("user-agent")

    def log(self, action: str, entity_type: str, entity_id=None, *, old=None, new=None,
            reason: str | None = None, commit: bool = True):
        return log_action(
            self.db,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            user=self.user,
            old_value=old,
            new_value=new,
            reason=reason,
            ip_address=self.ip,
            user_agent=self.ua,
            commit=commit,
        )


def audit_ctx(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AuditContext:
    return AuditContext(request, user, db)


def get_study_or_404(db: Session, study_id: str) -> ResearchStudy:
    study = db.get(ResearchStudy, study_id)
    if study is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Study '{study_id}' not found"
        )
    return study


def get_scoped_study(db: Session, user: User, study_id: str) -> ResearchStudy:
    study = get_study_or_404(db, study_id)
    assert_study_access(db, user, study)
    return study


def deny_read_only(user: User) -> None:
    """Regulators have statutory read-only access across the whole system."""
    if user.role == "regulator":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Regulator accounts are read-only by design (inspection access).",
        )


def get_or_404(db: Session, model, obj_id: str, label: str):
    obj = db.get(model, obj_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"{label} '{obj_id}' not found")
    return obj
