"""Patient management: screening, enrollment (CTRI hard gate), withdrawal."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import AuditContext, audit_ctx, get_scoped_study
from ..models import AdverseEvent, Patient, User, VisitLog
from ..rules import (
    accessible_study_ids,
    assert_enrollment_allowed,
    assert_not_locked,
    assert_study_access,
    next_randomization_number,
    next_screening_number,
)
from ..schemas import (
    Paged,
    PatientEnroll,
    PatientOut,
    PatientScreen,
    PatientUpdate,
    PatientWithdraw,
    ScreenFailRequest,
)
from ..security import RequireRoles, get_current_user

router = APIRouter(prefix="/patients", tags=["Patients"])
WRITE_ROLES = RequireRoles("coordinator", "pi", "admin")


def _get_patient(db: Session, user: User, patient_id: str) -> Patient:
    p = db.get(Patient, patient_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    assert_study_access(db, user, p.study)
    return p


@router.get("", response_model=Paged)
def list_patients(
    study_id: str | None = None,
    status: str | None = None,
    site_id: str | None = None,
    search: str | None = None,
    limit: int = Query(100, le=500),
    offset: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = select(Patient)
    scoped = accessible_study_ids(db, user)
    if scoped is not None:
        if not scoped:
            return Paged(total=0, limit=limit, offset=offset, items=[])
        stmt = stmt.where(Patient.study_id.in_(scoped))
    if study_id:
        stmt = stmt.where(Patient.study_id == study_id)
    if status:
        stmt = stmt.where(Patient.status == status)
    if site_id:
        stmt = stmt.where(Patient.site_id == site_id)
    if search:
        like = f"%{search.lower()}%"
        stmt = stmt.where(
            func.lower(Patient.screening_number).like(like)
            | func.lower(func.coalesce(Patient.randomization_number, "")).like(like)
        )
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = (
        db.execute(stmt.order_by(Patient.screening_number).limit(limit).offset(offset))
        .scalars()
        .all()
    )
    return Paged(
        total=total, limit=limit, offset=offset,
        items=[PatientOut.model_validate(p).model_dump() for p in rows],
    )


@router.post("/screen", response_model=PatientOut, status_code=201)
def screen_patient(
    payload: PatientScreen,
    ctx: AuditContext = Depends(audit_ctx),
    user: User = Depends(WRITE_ROLES),
):
    """Screen a subject. Screening is allowed once the site is activated."""
    db = ctx.db
    study = get_scoped_study(db, user, payload.study_id)
    assert_not_locked(study)
    if study.status not in ("active", "ctri_registered"):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "SCREENING_GATE_BLOCKED",
                "message": (
                    f"Screening requires the study to be 'ctri_registered' or 'active'. "
                    f"Current status: '{study.status}'."
                ),
            },
        )
    p = Patient(
        study_id=study.id,
        screening_number=payload.screening_number or next_screening_number(db, study),
        subject_initials=payload.subject_initials,
        screening_date=payload.screening_date or date.today(),
        status="screened",
        age=payload.age,
        sex=payload.sex,
        site_id=study.site_id,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    ctx.log("SCREEN", "Patient", p.id, new=p, reason=f"Subject screened for {study.protocol_number}")
    return PatientOut.model_validate(p)


@router.post("/{patient_id}/enroll", response_model=PatientOut)
def enroll_patient(
    patient_id: str,
    payload: PatientEnroll,
    ctx: AuditContext = Depends(audit_ctx),
    user: User = Depends(WRITE_ROLES),
):
    """Enroll a screened subject. Enforces the CTRI + IEC + activation HARD GATE."""
    db = ctx.db
    p = _get_patient(db, user, patient_id)
    study = p.study
    if p.status != "screened":
        raise HTTPException(
            status_code=409,
            detail=f"Only subjects with status 'screened' can be enrolled (current: '{p.status}')",
        )
    if not payload.consent_obtained:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CONSENT_REQUIRED",
                "message": (
                    "Written informed consent must be obtained before enrollment "
                    "(ICH-GCP 4.8 / NDCT Rules 2019)."
                ),
            },
        )
    # >>> regulatory hard gate <<<
    assert_enrollment_allowed(study, db)

    before = PatientOut.model_validate(p).model_dump()
    p.status = "enrolled"
    p.enrollment_date = payload.enrollment_date or date.today()
    p.randomization_number = payload.randomization_number or next_randomization_number(db, study)
    p.arm = payload.arm
    p.consent_obtained = True
    p.consent_date = payload.consent_date or p.screening_date or date.today()
    p.consent_version = payload.consent_version
    p.consent_language = payload.consent_language
    study.enrolled_count = (study.enrolled_count or 0) + 1
    db.commit()
    db.refresh(p)
    ctx.log(
        "ENROLL", "Patient", p.id, old=before, new=p,
        reason=(
            f"Enrolled under CTRI {study.ctri_registration_number} / "
            f"IEC {study.iec_approval_number}"
        ),
    )
    return PatientOut.model_validate(p)


@router.get("/{patient_id}", response_model=PatientOut)
def get_patient(patient_id: str, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    return PatientOut.model_validate(_get_patient(db, user, patient_id))


@router.put("/{patient_id}", response_model=PatientOut)
def update_patient(patient_id: str, payload: PatientUpdate,
                   ctx: AuditContext = Depends(audit_ctx),
                   user: User = Depends(WRITE_ROLES)):
    db = ctx.db
    p = _get_patient(db, user, patient_id)
    assert_not_locked(p.study)
    before = PatientOut.model_validate(p).model_dump()
    for f, v in payload.model_dump(exclude_unset=True).items():
        setattr(p, f, v)
    db.commit()
    db.refresh(p)
    ctx.log("UPDATE", "Patient", p.id, old=before,
            new=PatientOut.model_validate(p).model_dump())
    return PatientOut.model_validate(p)


@router.post("/{patient_id}/screen-fail", response_model=PatientOut)
def screen_fail(patient_id: str, payload: ScreenFailRequest,
                ctx: AuditContext = Depends(audit_ctx),
                user: User = Depends(WRITE_ROLES)):
    db = ctx.db
    p = _get_patient(db, user, patient_id)
    if p.status != "screened":
        raise HTTPException(status_code=409,
                            detail=f"Only screened subjects can be screen-failed (current: {p.status})")
    old = {"status": p.status}
    p.status = "screen_failed"
    p.screen_failure_reason = payload.screen_failure_reason
    db.commit()
    db.refresh(p)
    ctx.log("SCREEN_FAIL", "Patient", p.id, old=old,
            new={"status": p.status, "reason": payload.screen_failure_reason},
            reason=payload.screen_failure_reason)
    return PatientOut.model_validate(p)


@router.post("/{patient_id}/withdraw", response_model=PatientOut)
def withdraw(patient_id: str, payload: PatientWithdraw,
             ctx: AuditContext = Depends(audit_ctx),
             user: User = Depends(WRITE_ROLES)):
    db = ctx.db
    p = _get_patient(db, user, patient_id)
    if p.status != "enrolled":
        raise HTTPException(status_code=409,
                            detail=f"Only enrolled subjects can withdraw (current: {p.status})")
    old = {"status": p.status}
    p.status = "withdrawn"
    p.withdrawal_reason = payload.withdrawal_reason
    p.completion_date = payload.date or date.today()
    db.commit()
    db.refresh(p)
    ctx.log("WITHDRAW", "Patient", p.id, old=old,
            new={"status": p.status, "withdrawal_reason": payload.withdrawal_reason},
            reason=payload.withdrawal_reason)
    return PatientOut.model_validate(p)


@router.post("/{patient_id}/complete", response_model=PatientOut)
def complete(patient_id: str, completion_date: date | None = None,
             ctx: AuditContext = Depends(audit_ctx),
             user: User = Depends(WRITE_ROLES)):
    db = ctx.db
    p = _get_patient(db, user, patient_id)
    if p.status != "enrolled":
        raise HTTPException(status_code=409,
                            detail=f"Only enrolled subjects can complete (current: {p.status})")
    old = {"status": p.status}
    p.status = "completed"
    p.completion_date = completion_date or date.today()
    db.commit()
    db.refresh(p)
    ctx.log("COMPLETE", "Patient", p.id, old=old,
            new={"status": p.status, "completion_date": p.completion_date})
    return PatientOut.model_validate(p)


@router.get("/{patient_id}/timeline")
def patient_timeline(patient_id: str, db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    p = _get_patient(db, user, patient_id)
    visits = (
        db.execute(
            select(VisitLog).where(VisitLog.patient_id == p.id).order_by(VisitLog.visit_number)
        )
        .scalars()
        .all()
    )
    aes = (
        db.execute(
            select(AdverseEvent)
            .where(AdverseEvent.patient_id == p.id)
            .order_by(AdverseEvent.onset_date)
        )
        .scalars()
        .all()
    )
    return {
        "patient": PatientOut.model_validate(p).model_dump(),
        "visits": [
            {
                "id": v.id,
                "visit_number": v.visit_number,
                "visit_name": v.visit_name,
                "scheduled_date": v.scheduled_date,
                "actual_date": v.actual_date,
                "status": v.status,
                "deviation_flag": v.deviation_flag,
            }
            for v in visits
        ],
        "adverse_events": [
            {
                "id": a.id,
                "ae_number": a.ae_number,
                "ae_term": a.ae_term,
                "seriousness": a.seriousness,
                "severity": a.severity,
                "onset_date": a.onset_date,
                "status": a.status,
            }
            for a in aes
        ],
    }
