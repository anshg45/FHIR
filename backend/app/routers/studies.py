"""Study Tracker: lifecycle, CTRI/IEC gates, KPIs, milestones, assignments."""
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import AuditContext, audit_ctx, deny_read_only, get_scoped_study, get_study_or_404
from ..models import (
    AdverseEvent,
    DataQuery,
    Milestone,
    Patient,
    ProtocolDeviation,
    ResearchStudy,
    Site,
    StudyAssignment,
    User,
    VisitLog,
)
from ..rules import accessible_study_ids, assert_not_locked, assert_transition
from ..schemas import (
    AssignmentRequest,
    CloseoutRequest,
    CtriRegistrationRequest,
    EcDecisionRequest,
    MilestoneCreate,
    MilestoneOut,
    Paged,
    StatusChangeRequest,
    StudyCreate,
    StudyOut,
    StudyUpdate,
)
from ..security import RequireRoles, get_current_user

router = APIRouter(prefix="/studies", tags=["Study Tracker"])

DEFAULT_MILESTONES = [
    ("Protocol finalisation", 0, "pi"),
    ("IEC submission", 14, "pi"),
    ("IEC approval", 45, "ec"),
    ("CTRI registration", 60, "pi"),
    ("Site activation", 75, "admin"),
    ("First subject enrolled", 90, "coordinator"),
    ("50% enrollment", 180, "coordinator"),
    ("Last subject enrolled", 300, "coordinator"),
    ("Database lock", 360, "admin"),
    ("SDTM export / regulatory submission", 390, "admin"),
]


# --------------------------------------------------------------------- CRUD
@router.get("", response_model=Paged)
def list_studies(
    status: str | None = None,
    ctri_status: str | None = None,
    iec_approval_status: str | None = None,
    site_id: str | None = None,
    phase: str | None = None,
    search: str | None = None,
    limit: int = Query(50, le=500),
    offset: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = select(ResearchStudy)
    scoped = accessible_study_ids(db, user)
    if scoped is not None:
        if not scoped:
            return Paged(total=0, limit=limit, offset=offset, items=[])
        stmt = stmt.where(ResearchStudy.id.in_(scoped))
    if status:
        stmt = stmt.where(ResearchStudy.status == status)
    if ctri_status:
        stmt = stmt.where(ResearchStudy.ctri_status == ctri_status)
    if iec_approval_status:
        stmt = stmt.where(ResearchStudy.iec_approval_status == iec_approval_status)
    if site_id:
        stmt = stmt.where(ResearchStudy.site_id == site_id)
    if phase:
        stmt = stmt.where(ResearchStudy.phase == phase)
    if search:
        like = f"%{search.lower()}%"
        stmt = stmt.where(
            func.lower(ResearchStudy.title).like(like)
            | func.lower(ResearchStudy.protocol_number).like(like)
        )
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = (
        db.execute(stmt.order_by(ResearchStudy.created_at.desc()).limit(limit).offset(offset))
        .scalars()
        .all()
    )
    return Paged(
        total=total, limit=limit, offset=offset,
        items=[StudyOut.model_validate(r).model_dump() for r in rows],
    )


@router.post("", response_model=StudyOut, status_code=201)
def create_study(
    payload: StudyCreate,
    generate_milestones: bool = True,
    ctx: AuditContext = Depends(audit_ctx),
    user: User = Depends(RequireRoles("admin", "pi")),
):
    db = ctx.db
    if (
        db.execute(
            select(ResearchStudy).where(
                ResearchStudy.protocol_number == payload.protocol_number
            )
        )
        .scalars()
        .first()
    ):
        raise HTTPException(status_code=409, detail="Protocol number already exists")
    if payload.site_id and db.get(Site, payload.site_id) is None:
        raise HTTPException(status_code=400, detail="site_id does not exist")

    data = payload.model_dump()
    if not data.get("principal_investigator_id") and user.role == "pi":
        data["principal_investigator_id"] = user.id
    study = ResearchStudy(**data, status="draft")
    db.add(study)
    db.commit()
    db.refresh(study)

    if generate_milestones:
        base = study.start_date or date.today()
        for mtype, offset_days, owner in DEFAULT_MILESTONES:
            db.add(
                Milestone(
                    study_id=study.id,
                    milestone_type=mtype,
                    due_date=base + timedelta(days=offset_days),
                    owner_role=owner,
                )
            )
        db.commit()

    ctx.log("CREATE", "ResearchStudy", study.id, new=study,
            reason="New study registered in CTMS")
    return StudyOut.model_validate(study)


@router.get("/{study_id}", response_model=StudyOut)
def get_study(study_id: str, db: Session = Depends(get_db),
              user: User = Depends(get_current_user)):
    return StudyOut.model_validate(get_scoped_study(db, user, study_id))


@router.put("/{study_id}", response_model=StudyOut)
def update_study(
    study_id: str,
    payload: StudyUpdate,
    ctx: AuditContext = Depends(audit_ctx),
    user: User = Depends(RequireRoles("admin", "pi")),
):
    db = ctx.db
    study = get_scoped_study(db, user, study_id)
    assert_not_locked(study)
    before = StudyOut.model_validate(study).model_dump()
    for f, v in payload.model_dump(exclude_unset=True).items():
        setattr(study, f, v)
    db.commit()
    db.refresh(study)
    ctx.log("UPDATE", "ResearchStudy", study.id, old=before,
            new=StudyOut.model_validate(study).model_dump())
    return StudyOut.model_validate(study)


@router.delete("/{study_id}")
def delete_study(study_id: str, ctx: AuditContext = Depends(audit_ctx),
                 _: User = Depends(RequireRoles("admin"))):
    db = ctx.db
    study = get_study_or_404(db, study_id)
    if study.status != "draft":
        raise HTTPException(
            status_code=409,
            detail="Only draft studies can be deleted. Use terminate for active studies.",
        )
    snapshot = StudyOut.model_validate(study).model_dump()
    db.delete(study)
    db.commit()
    ctx.log("DELETE", "ResearchStudy", study_id, old=snapshot)
    return {"success": True, "deleted_study_id": study_id}


# --------------------------------------------------------- lifecycle actions
@router.post("/{study_id}/submit-protocol", response_model=StudyOut)
def submit_protocol(study_id: str, ctx: AuditContext = Depends(audit_ctx),
                    user: User = Depends(RequireRoles("pi", "admin"))):
    db = ctx.db
    study = get_scoped_study(db, user, study_id)
    assert_transition(study.status, "protocol_submitted")
    old = study.status
    study.status = "protocol_submitted"
    study.iec_approval_status = "pending"
    db.commit()
    db.refresh(study)
    ctx.log("SUBMIT_PROTOCOL", "ResearchStudy", study.id,
            old={"status": old}, new={"status": study.status, "iec_approval_status": "pending"},
            reason="Protocol submitted to the Institutional Ethics Committee")
    return StudyOut.model_validate(study)


@router.post("/{study_id}/ec-decision", response_model=StudyOut)
def ec_decision(
    study_id: str,
    payload: EcDecisionRequest,
    ctx: AuditContext = Depends(audit_ctx),
    _: User = Depends(RequireRoles("ec", "admin")),
):
    db = ctx.db
    study = get_study_or_404(db, study_id)
    if study.status != "protocol_submitted":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Study status is '{study.status}'. An EC decision is only valid for "
                "'protocol_submitted'."
            ),
        )
    old = {"status": study.status, "iec_approval_status": study.iec_approval_status}
    if payload.decision == "approved":
        assert_transition(study.status, "ec_approved")
        study.status = "ec_approved"
        study.iec_approval_status = "approved"
        study.iec_approval_number = payload.iec_approval_number
        study.iec_approval_date = payload.iec_approval_date or date.today()
        study.iec_renewal_due = payload.iec_renewal_due or (
            (payload.iec_approval_date or date.today()) + timedelta(days=365)
        )
        _complete_milestone(db, study.id, "IEC approval")
    else:
        assert_transition(study.status, "ec_rejected")
        study.status = "ec_rejected"
        study.iec_approval_status = "rejected"
    study.iec_remarks = payload.remarks
    db.commit()
    db.refresh(study)
    ctx.log(
        "EC_DECISION", "ResearchStudy", study.id, old=old,
        new={
            "status": study.status,
            "iec_approval_status": study.iec_approval_status,
            "iec_approval_number": study.iec_approval_number,
            "iec_approval_date": study.iec_approval_date,
            "iec_renewal_due": study.iec_renewal_due,
        },
        reason=payload.remarks or f"Ethics Committee {payload.decision} the protocol",
    )
    return StudyOut.model_validate(study)


@router.post("/{study_id}/ctri-registration", response_model=StudyOut)
def ctri_registration(
    study_id: str,
    payload: CtriRegistrationRequest,
    ctx: AuditContext = Depends(audit_ctx),
    user: User = Depends(RequireRoles("pi", "admin")),
):
    """CTRI offers no public write/sync API, so the registration number issued by
    ctri.nic.in is recorded manually here and becomes the enrollment gate key."""
    db = ctx.db
    study = get_scoped_study(db, user, study_id)
    if study.iec_approval_status != "approved":
        raise HTTPException(
            status_code=409,
            detail="IEC approval must be recorded before CTRI registration details are entered.",
        )
    old = {
        "ctri_status": study.ctri_status,
        "ctri_registration_number": study.ctri_registration_number,
        "status": study.status,
    }
    study.ctri_registration_number = payload.ctri_registration_number
    study.ctri_status = payload.ctri_status
    study.ctri_registration_date = payload.ctri_registration_date or date.today()
    if payload.ctri_status == "registered" and study.status == "ec_approved":
        assert_transition(study.status, "ctri_registered")
        study.status = "ctri_registered"
        _complete_milestone(db, study.id, "CTRI registration")
    db.commit()
    db.refresh(study)
    ctx.log(
        "CTRI_REGISTRATION", "ResearchStudy", study.id, old=old,
        new={
            "ctri_status": study.ctri_status,
            "ctri_registration_number": study.ctri_registration_number,
            "ctri_registration_date": study.ctri_registration_date,
            "status": study.status,
        },
        reason="CTRI registration details recorded manually (no public CTRI write API)",
    )
    return StudyOut.model_validate(study)


@router.post("/{study_id}/status", response_model=StudyOut)
def change_status(
    study_id: str,
    payload: StatusChangeRequest,
    ctx: AuditContext = Depends(audit_ctx),
    user: User = Depends(RequireRoles("admin", "pi")),
):
    db = ctx.db
    study = get_scoped_study(db, user, study_id)
    assert_transition(study.status, payload.target_status)
    if payload.target_status == "active":
        problems = []
        if study.iec_approval_status != "approved":
            problems.append("IEC approval is not recorded")
        if study.ctri_status != "registered":
            problems.append("CTRI registration is not recorded")
        if problems:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "ACTIVATION_GATE_BLOCKED",
                    "message": "Cannot activate the study: " + "; ".join(problems),
                },
            )
        _complete_milestone(db, study.id, "Site activation")
    old = study.status
    study.status = payload.target_status
    db.commit()
    db.refresh(study)
    ctx.log("STATUS_CHANGE", "ResearchStudy", study.id, old={"status": old},
            new={"status": study.status}, reason=payload.reason)
    return StudyOut.model_validate(study)


@router.post("/{study_id}/lock-database", response_model=StudyOut)
def lock_database(study_id: str, ctx: AuditContext = Depends(audit_ctx),
                  _: User = Depends(RequireRoles("admin"))):
    db = ctx.db
    study = get_study_or_404(db, study_id)
    study.data_locked = True
    db.commit()
    db.refresh(study)
    _complete_milestone(db, study.id, "Database lock")
    ctx.log("LOCK_DATABASE", "ResearchStudy", study.id, old={"data_locked": False},
            new={"data_locked": True}, reason="Trial database locked for close-out")
    return StudyOut.model_validate(study)


@router.post("/{study_id}/unlock-database", response_model=StudyOut)
def unlock_database(study_id: str, reason: str = Query(min_length=5),
                    ctx: AuditContext = Depends(audit_ctx),
                    _: User = Depends(RequireRoles("admin"))):
    db = ctx.db
    study = get_study_or_404(db, study_id)
    study.data_locked = False
    db.commit()
    db.refresh(study)
    ctx.log("UNLOCK_DATABASE", "ResearchStudy", study.id, old={"data_locked": True},
            new={"data_locked": False}, reason=reason)
    return StudyOut.model_validate(study)


@router.post("/{study_id}/closeout", response_model=StudyOut)
def closeout(
    study_id: str,
    payload: CloseoutRequest,
    ctx: AuditContext = Depends(audit_ctx),
    user: User = Depends(RequireRoles("pi", "admin")),
):
    db = ctx.db
    study = get_scoped_study(db, user, study_id)
    if not study.data_locked:
        raise HTTPException(
            status_code=409, detail="Lock the trial database before signing close-out."
        )
    study.closeout_signed_by = payload.signed_by
    study.closeout_signed_at = datetime.now(timezone.utc)
    if study.status in ("active", "suspended"):
        study.status = "completed"
    db.commit()
    db.refresh(study)
    ctx.log(
        "CLOSEOUT_SIGNOFF", "ResearchStudy", study.id,
        new={
            "closeout_signed_by": payload.signed_by,
            "statement": payload.statement,
            "status": study.status,
        },
        reason=(
            "PI close-out sign-off. ASSUMPTION: this is an auditable in-system "
            "attestation, not a legally valid digital signature (that requires a "
            "licensed Certifying Authority under the IT Act 2000)."
        ),
    )
    return StudyOut.model_validate(study)


# ------------------------------------------------------------------ team
@router.get("/{study_id}/team")
def study_team(study_id: str, db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    study = get_scoped_study(db, user, study_id)
    rows = (
        db.execute(
            select(StudyAssignment, User)
            .join(User, User.id == StudyAssignment.user_id)
            .where(StudyAssignment.study_id == study.id)
        )
        .all()
    )
    return {
        "study_id": study.id,
        "principal_investigator": (
            {
                "id": study.principal_investigator.id,
                "name": study.principal_investigator.name,
                "email": study.principal_investigator.email,
            }
            if study.principal_investigator
            else None
        ),
        "assignments": [
            {
                "user_id": u.id,
                "name": u.name,
                "email": u.email,
                "role": u.role,
                "role_on_study": a.role_on_study,
            }
            for a, u in rows
        ],
    }


@router.post("/{study_id}/assign", status_code=201)
def assign_user(
    study_id: str,
    payload: AssignmentRequest,
    ctx: AuditContext = Depends(audit_ctx),
    _: User = Depends(RequireRoles("admin", "pi")),
):
    db = ctx.db
    study = get_study_or_404(db, study_id)
    target = db.get(User, payload.user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    existing = (
        db.execute(
            select(StudyAssignment).where(
                StudyAssignment.study_id == study.id,
                StudyAssignment.user_id == target.id,
            )
        )
        .scalars()
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="User is already assigned to this study")
    a = StudyAssignment(
        study_id=study.id, user_id=target.id,
        role_on_study=payload.role_on_study or target.role,
    )
    db.add(a)
    db.commit()
    ctx.log("ASSIGN_USER", "ResearchStudy", study.id,
            new={"user_id": target.id, "email": target.email,
                 "role_on_study": a.role_on_study})
    return {"success": True, "study_id": study.id, "user_id": target.id}


@router.delete("/{study_id}/assign/{user_id}")
def unassign_user(study_id: str, user_id: str, ctx: AuditContext = Depends(audit_ctx),
                  _: User = Depends(RequireRoles("admin", "pi"))):
    db = ctx.db
    a = (
        db.execute(
            select(StudyAssignment).where(
                StudyAssignment.study_id == study_id, StudyAssignment.user_id == user_id
            )
        )
        .scalars()
        .first()
    )
    if a is None:
        raise HTTPException(status_code=404, detail="Assignment not found")
    db.delete(a)
    db.commit()
    ctx.log("UNASSIGN_USER", "ResearchStudy", study_id, old={"user_id": user_id})
    return {"success": True}


# ------------------------------------------------------------------- KPIs
@router.get("/{study_id}/kpis")
def study_kpis(study_id: str, db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    study = get_scoped_study(db, user, study_id)

    def count(model, *where):
        return db.execute(
            select(func.count()).select_from(model).where(*where)
        ).scalar_one()

    screened = count(Patient, Patient.study_id == study.id)
    enrolled = count(Patient, Patient.study_id == study.id,
                     Patient.status.in_(["enrolled", "completed", "withdrawn"]))
    active_pts = count(Patient, Patient.study_id == study.id, Patient.status == "enrolled")
    completed = count(Patient, Patient.study_id == study.id, Patient.status == "completed")
    withdrawn = count(Patient, Patient.study_id == study.id, Patient.status == "withdrawn")
    screen_failed = count(Patient, Patient.study_id == study.id,
                          Patient.status == "screen_failed")

    visits_total = count(VisitLog, VisitLog.study_id == study.id)
    visits_completed = count(VisitLog, VisitLog.study_id == study.id,
                             VisitLog.status == "completed")
    visits_missed = count(VisitLog, VisitLog.study_id == study.id, VisitLog.status == "missed")
    visits_overdue = count(VisitLog, VisitLog.study_id == study.id,
                           VisitLog.status == "overdue")

    ae_total = count(AdverseEvent, AdverseEvent.study_id == study.id)
    sae_total = count(AdverseEvent, AdverseEvent.study_id == study.id,
                      AdverseEvent.seriousness == "serious")
    ae_open = count(AdverseEvent, AdverseEvent.study_id == study.id,
                    AdverseEvent.status.in_(["open", "under_review"]))
    now = datetime.now(timezone.utc)
    ae_breached = count(
        AdverseEvent,
        AdverseEvent.study_id == study.id,
        AdverseEvent.regulatory_deadline.isnot(None),
        AdverseEvent.regulatory_deadline < now,
        AdverseEvent.reported_to_authority_at.is_(None),
    )

    dev_total = count(ProtocolDeviation, ProtocolDeviation.study_id == study.id)
    dev_critical = count(ProtocolDeviation, ProtocolDeviation.study_id == study.id,
                         ProtocolDeviation.severity == "critical")
    q_open = count(DataQuery, DataQuery.study_id == study.id, DataQuery.status == "open")
    ms_overdue = count(Milestone, Milestone.study_id == study.id,
                       Milestone.status == "overdue")

    target = study.enrollment_target or 0
    return {
        "study_id": study.id,
        "protocol_number": study.protocol_number,
        "title": study.title,
        "status": study.status,
        "gates": {
            "iec_approval_status": study.iec_approval_status,
            "ctri_status": study.ctri_status,
            "ctri_registration_number": study.ctri_registration_number,
            "enrollment_permitted": (
                study.iec_approval_status == "approved"
                and study.ctri_status == "registered"
                and study.status == "active"
                and not study.data_locked
            ),
            "data_locked": study.data_locked,
        },
        "enrollment": {
            "target": target,
            "screened": screened,
            "enrolled": enrolled,
            "active": active_pts,
            "completed": completed,
            "withdrawn": withdrawn,
            "screen_failed": screen_failed,
            "percent_of_target": round(enrolled / target * 100, 1) if target else 0.0,
            "screen_failure_rate": round(screen_failed / screened * 100, 1) if screened else 0.0,
            "withdrawal_rate": round(withdrawn / enrolled * 100, 1) if enrolled else 0.0,
        },
        "visit_compliance": {
            "total": visits_total,
            "completed": visits_completed,
            "missed": visits_missed,
            "overdue": visits_overdue,
            "compliance_rate": (
                round(visits_completed / visits_total * 100, 1) if visits_total else 0.0
            ),
        },
        "safety": {
            "ae_total": ae_total,
            "sae_total": sae_total,
            "ae_open": ae_open,
            "deadlines_breached": ae_breached,
        },
        "quality": {
            "deviations_total": dev_total,
            "deviations_critical": dev_critical,
            "open_queries": q_open,
            "overdue_milestones": ms_overdue,
        },
    }


# -------------------------------------------------------------- milestones
@router.get("/{study_id}/milestones", response_model=list[MilestoneOut])
def list_milestones(study_id: str, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    study = get_scoped_study(db, user, study_id)
    rows = (
        db.execute(
            select(Milestone).where(Milestone.study_id == study.id).order_by(Milestone.due_date)
        )
        .scalars()
        .all()
    )
    return [MilestoneOut.model_validate(m) for m in rows]


@router.post("/{study_id}/milestones", response_model=MilestoneOut, status_code=201)
def create_milestone(
    study_id: str,
    payload: MilestoneCreate,
    ctx: AuditContext = Depends(audit_ctx),
    user: User = Depends(RequireRoles("admin", "pi")),
):
    db = ctx.db
    study = get_scoped_study(db, user, study_id)
    m = Milestone(**{**payload.model_dump(), "study_id": study.id})
    db.add(m)
    db.commit()
    db.refresh(m)
    ctx.log("CREATE", "Milestone", m.id, new=m)
    return MilestoneOut.model_validate(m)


def _complete_milestone(db: Session, study_id: str, milestone_type: str) -> None:
    m = (
        db.execute(
            select(Milestone).where(
                Milestone.study_id == study_id,
                Milestone.milestone_type == milestone_type,
                Milestone.status != "completed",
            )
        )
        .scalars()
        .first()
    )
    if m:
        m.status = "completed"
        m.completed_date = date.today()
        db.commit()
