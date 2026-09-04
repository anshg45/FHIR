"""Business rules / regulatory gates enforced server-side."""
from datetime import date

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Patient, ResearchStudy, StudyAssignment, User

# study.status transitions that are legal
ALLOWED_TRANSITIONS = {
    "draft": {"protocol_submitted", "terminated"},
    "protocol_submitted": {"ec_approved", "ec_rejected", "draft", "terminated"},
    "ec_rejected": {"draft", "protocol_submitted", "terminated"},
    "ec_approved": {"ctri_registered", "terminated", "suspended"},
    "ctri_registered": {"active", "terminated", "suspended"},
    "active": {"suspended", "completed", "terminated"},
    "suspended": {"active", "terminated", "completed"},
    "completed": set(),
    "terminated": set(),
}


class GateError(HTTPException):
    def __init__(self, detail: str, code: str = "REGULATORY_GATE_BLOCKED"):
        super().__init__(
            status_code=status.HTTP_409_CONFLICT, detail={"code": code, "message": detail}
        )


def assert_transition(current: str, target: str) -> None:
    allowed = ALLOWED_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise GateError(
            f"Illegal study status transition '{current}' -> '{target}'. "
            f"Allowed from '{current}': {sorted(allowed) or 'none (terminal state)'}",
            code="ILLEGAL_STATUS_TRANSITION",
        )


def assert_enrollment_allowed(study: ResearchStudy, db: Session) -> None:
    """CTRI hard gate.

    Indian law (Clinical Trials Registry - India, mandatory since 2009) requires
    prospective registration BEFORE the first subject is enrolled. This gate is
    enforced in the backend so it cannot be bypassed from any client.
    """
    problems = []
    if study.iec_approval_status != "approved":
        problems.append(
            f"Institutional Ethics Committee approval is '{study.iec_approval_status}' "
            "(must be 'approved')"
        )
    if study.ctri_status != "registered" or not study.ctri_registration_number:
        problems.append(
            f"CTRI registration is '{study.ctri_status}' "
            "(must be 'registered' with a CTRI number)"
        )
    if study.status not in ("active",):
        problems.append(f"Study status is '{study.status}' (must be 'active')")
    if study.iec_renewal_due and study.iec_renewal_due < date.today():
        problems.append(
            f"IEC approval expired on {study.iec_renewal_due.isoformat()} - renewal required"
        )
    if study.data_locked:
        problems.append("Study database is locked (close-out completed)")
    if study.enrollment_target and study.enrolled_count >= study.enrollment_target:
        problems.append(
            f"Enrollment target reached ({study.enrolled_count}/{study.enrollment_target})"
        )
    if problems:
        raise GateError(
            "Enrollment blocked by regulatory gate: " + "; ".join(problems),
            code="ENROLLMENT_GATE_BLOCKED",
        )


def assert_not_locked(study: ResearchStudy) -> None:
    if study.data_locked:
        raise GateError(
            f"Study '{study.protocol_number}' database is LOCKED. No data modification allowed.",
            code="DATABASE_LOCKED",
        )


def user_can_access_study(db: Session, user: User, study: ResearchStudy) -> bool:
    """Row-level scoping: who may see/act on a given study."""
    if user.role in ("admin", "regulator", "ec", "pv"):
        return True
    if user.role == "pi":
        return study.principal_investigator_id == user.id or _assigned(db, user, study)
    if user.role in ("coordinator", "monitor"):
        return _assigned(db, user, study) or (
            user.site_id is not None and study.site_id == user.site_id
        )
    return False


def _assigned(db: Session, user: User, study: ResearchStudy) -> bool:
    return (
        db.execute(
            select(func.count())
            .select_from(StudyAssignment)
            .where(StudyAssignment.study_id == study.id, StudyAssignment.user_id == user.id)
        ).scalar_one()
        > 0
    )


def assert_study_access(db: Session, user: User, study: ResearchStudy) -> None:
    if not user_can_access_study(db, user, study):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"User '{user.email}' ({user.role}) is not assigned to study "
                f"'{study.protocol_number}'"
            ),
        )


def accessible_study_ids(db: Session, user: User) -> list[str] | None:
    """None => unrestricted (admin / regulator / ec / pv)."""
    if user.role in ("admin", "regulator", "ec", "pv"):
        return None
    ids = set(
        db.execute(select(StudyAssignment.study_id).where(StudyAssignment.user_id == user.id))
        .scalars()
        .all()
    )
    if user.role == "pi":
        ids |= set(
            db.execute(
                select(ResearchStudy.id).where(ResearchStudy.principal_investigator_id == user.id)
            )
            .scalars()
            .all()
        )
    if user.role in ("coordinator", "monitor") and user.site_id:
        ids |= set(
            db.execute(select(ResearchStudy.id).where(ResearchStudy.site_id == user.site_id))
            .scalars()
            .all()
        )
    return list(ids)


def next_screening_number(db: Session, study: ResearchStudy) -> str:
    count = db.execute(
        select(func.count()).select_from(Patient).where(Patient.study_id == study.id)
    ).scalar_one()
    return f"{study.protocol_number}-S{count + 1:04d}"


def next_randomization_number(db: Session, study: ResearchStudy) -> str:
    count = db.execute(
        select(func.count())
        .select_from(Patient)
        .where(Patient.study_id == study.id, Patient.randomization_number.isnot(None))
    ).scalar_one()
    return f"{study.protocol_number}-R{count + 1:04d}"
