"""Pharmacovigilance module (AIIA NPvCC): AE/SAE capture, coding, routing,
regulatory deadline tracking, DSMB feed."""
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import meddra
from ..database import get_db
from ..deadlines import compute_deadlines, deadline_state
from ..deps import AuditContext, audit_ctx, get_scoped_study
from ..models import AdverseEvent, Alert, Patient, User
from ..rules import accessible_study_ids, assert_not_locked, assert_study_access
from ..schemas import (
    AeCodeRequest,
    AeCreate,
    AeOut,
    AeReportedRequest,
    AeUpdate,
    Paged,
    SaeConfirmRequest,
)
from ..security import RequireRoles, get_current_user

router = APIRouter(prefix="/ae", tags=["Pharmacovigilance (AE/SAE)"])
CAPTURE_ROLES = RequireRoles("coordinator", "pi", "pv", "admin")
PV_ROLES = RequireRoles("pv", "admin")


def _get_ae(db: Session, user: User, ae_id: str) -> AdverseEvent:
    ae = db.get(AdverseEvent, ae_id)
    if ae is None:
        raise HTTPException(status_code=404, detail="Adverse event not found")
    assert_study_access(db, user, ae.study)
    return ae


def _with_deadline(ae: AdverseEvent) -> dict:
    out = AeOut.model_validate(ae).model_dump()
    out["deadline_state"] = deadline_state(ae.regulatory_deadline)
    out["followup_deadline_state"] = deadline_state(ae.followup_deadline)
    return out


# ---------------------------------------------------------------- dictionary
@router.get("/dictionary/search")
def dictionary_search(term: str, limit: int = 8, _: User = Depends(get_current_user)):
    """Search the SYNTHETIC MedDRA-shaped dictionary."""
    return {
        "dictionary": meddra.DICTIONARY_NAME,
        "assumption": (
            "Real MedDRA / WHODrug require paid subscription licences and cannot be "
            "redistributed. This is a clearly-labelled synthetic stub with "
            "MedDRA-shaped fields (PT code, Preferred Term, SOC)."
        ),
        "results": meddra.search(term, limit=limit),
    }


@router.get("/dictionary/soc")
def dictionary_soc(_: User = Depends(get_current_user)):
    return {"dictionary": meddra.DICTIONARY_NAME, "system_organ_classes": meddra.soc_list()}


# --------------------------------------------------------------------- CRUD
@router.get("", response_model=Paged)
def list_aes(
    study_id: str | None = None,
    patient_id: str | None = None,
    seriousness: str | None = None,
    status: str | None = None,
    severity: str | None = None,
    unreported_only: bool = False,
    limit: int = Query(100, le=500),
    offset: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = select(AdverseEvent)
    scoped = accessible_study_ids(db, user)
    if scoped is not None:
        if not scoped:
            return Paged(total=0, limit=limit, offset=offset, items=[])
        stmt = stmt.where(AdverseEvent.study_id.in_(scoped))
    if study_id:
        stmt = stmt.where(AdverseEvent.study_id == study_id)
    if patient_id:
        stmt = stmt.where(AdverseEvent.patient_id == patient_id)
    if seriousness:
        stmt = stmt.where(AdverseEvent.seriousness == seriousness)
    if status:
        stmt = stmt.where(AdverseEvent.status == status)
    if severity:
        stmt = stmt.where(AdverseEvent.severity == severity)
    if unreported_only:
        stmt = stmt.where(AdverseEvent.reported_to_authority_at.is_(None))
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = (
        db.execute(
            stmt.order_by(AdverseEvent.regulatory_deadline.asc().nullslast())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return Paged(total=total, limit=limit, offset=offset,
                 items=[_with_deadline(a) for a in rows])


@router.post("", status_code=201)
def capture_ae(
    payload: AeCreate,
    ctx: AuditContext = Depends(audit_ctx),
    user: User = Depends(CAPTURE_ROLES),
):
    """Capture an AE / SAE. The regulatory reporting deadline is calculated
    automatically and the event is routed to Pharmacovigilance."""
    db = ctx.db
    study = get_scoped_study(db, user, payload.study_id)
    assert_not_locked(study)
    patient = db.get(Patient, payload.patient_id)
    if patient is None or patient.study_id != study.id:
        raise HTTPException(status_code=400, detail="patient_id does not belong to this study")

    seq = db.execute(
        select(func.count()).select_from(AdverseEvent).where(AdverseEvent.study_id == study.id)
    ).scalar_one()
    now = datetime.now(timezone.utc)
    d = compute_deadlines(
        seriousness=payload.seriousness,
        outcome=payload.outcome,
        seriousness_criteria=payload.seriousness_criteria,
        report_date=now,
    )

    data = payload.model_dump(exclude={"auto_code"})
    ae = AdverseEvent(
        **data,
        ae_number=f"{study.protocol_number}-AE{seq + 1:04d}",
        report_date=now,
        regulatory_deadline=d["regulatory_deadline"],
        followup_deadline=d["followup_deadline"],
        reported_by=user.id,
        status="open",
        dsmb_flag=payload.seriousness == "serious",
    )
    if ae.onset_date is None:
        ae.onset_date = date.today()
    if ae.ae_term is None:
        ae.ae_term = payload.description[:255]

    if payload.auto_code:
        hit = meddra.autocode(ae.ae_term)
        if hit:
            ae.meddra_code = hit["code"]
            ae.meddra_pt = hit["pt"]
            ae.meddra_soc = hit["soc"]
            ae.coding_dictionary = meddra.DICTIONARY_NAME
            ae.coded_at = now
            ae.status = "coded"

    db.add(ae)
    db.commit()
    db.refresh(ae)

    if ae.seriousness == "serious":
        db.add(
            Alert(
                study_id=study.id,
                alert_type="sae_reported",
                severity="critical",
                message=(
                    f"SAE {ae.ae_number} reported for subject {patient.screening_number}. "
                    f"Regulatory deadline: {ae.regulatory_deadline.isoformat()} ({d['rule']})."
                ),
                entity_type="AdverseEvent",
                entity_id=ae.id,
                target_roles=["pv", "ec", "admin", "pi"],
            )
        )
        db.commit()

    ctx.log(
        "CREATE_SAE" if ae.seriousness == "serious" else "CREATE_AE",
        "AdverseEvent", ae.id, new=ae, reason=d["rule"],
    )
    out = _with_deadline(ae)
    out["deadline_rule"] = d["rule"]
    out["routed_to"] = ["pv"] + (["ec", "dsmb"] if ae.seriousness == "serious" else [])
    return out


@router.get("/inbox", response_model=Paged)
def pv_inbox(
    limit: int = Query(100, le=500),
    offset: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(RequireRoles("pv", "admin", "ec", "regulator")),
):
    """Pharmacovigilance / NPvCC inbox: everything not yet reported to the authority."""
    stmt = select(AdverseEvent).where(AdverseEvent.reported_to_authority_at.is_(None))
    if user.role == "ec":
        stmt = stmt.where(AdverseEvent.ec_escalated.is_(True))
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = (
        db.execute(
            stmt.order_by(AdverseEvent.regulatory_deadline.asc().nullslast())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return Paged(total=total, limit=limit, offset=offset,
                 items=[_with_deadline(a) for a in rows])


@router.get("/deadlines")
def deadline_dashboard(db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    stmt = select(AdverseEvent).where(
        AdverseEvent.regulatory_deadline.isnot(None),
        AdverseEvent.reported_to_authority_at.is_(None),
    )
    scoped = accessible_study_ids(db, user)
    if scoped is not None:
        if not scoped:
            return {"breached": [], "due_soon": [], "on_track": [], "summary": {}}
        stmt = stmt.where(AdverseEvent.study_id.in_(scoped))
    rows = db.execute(stmt.order_by(AdverseEvent.regulatory_deadline)).scalars().all()
    buckets: dict[str, list] = {"breached": [], "due_soon": [], "on_track": []}
    for a in rows:
        st = deadline_state(a.regulatory_deadline)
        buckets.setdefault(st["state"], []).append(
            {
                "id": a.id,
                "ae_number": a.ae_number,
                "study_id": a.study_id,
                "patient_id": a.patient_id,
                "ae_term": a.ae_term,
                "seriousness": a.seriousness,
                "status": a.status,
                "regulatory_deadline": a.regulatory_deadline,
                "hours_remaining": st["hours_remaining"],
            }
        )
    return {
        **buckets,
        "summary": {k: len(v) for k, v in buckets.items()},
        "rules": {
            "sae_fatal_or_life_threatening": "24 hours initial + 14 days detailed",
            "other_serious": "15 calendar days",
            "non_serious": "30 calendar days",
        },
    }


@router.get("/dsmb-feed")
def dsmb_feed(
    study_id: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(RequireRoles("pv", "admin", "ec", "regulator", "pi")),
):
    """Data Safety Monitoring Board feed: SAEs grouped by System Organ Class.

    ASSUMPTION: real DSMB decisions are made by an independent institutional
    board; this endpoint only produces the safety data package they review.
    """
    stmt = select(AdverseEvent).where(AdverseEvent.dsmb_flag.is_(True))
    if study_id:
        stmt = stmt.where(AdverseEvent.study_id == study_id)
    rows = db.execute(stmt.order_by(AdverseEvent.onset_date.desc())).scalars().all()
    by_soc: dict[str, list] = {}
    for a in rows:
        by_soc.setdefault(a.meddra_soc or "Uncoded", []).append(
            {
                "ae_number": a.ae_number,
                "study_id": a.study_id,
                "patient_id": a.patient_id,
                "ae_term": a.ae_term,
                "meddra_code": a.meddra_code,
                "meddra_pt": a.meddra_pt,
                "severity": a.severity,
                "causality": a.causality,
                "outcome": a.outcome,
                "onset_date": a.onset_date,
                "sae_confirmed": a.sae_confirmed,
                "ec_escalated": a.ec_escalated,
            }
        )
    fatal = sum(1 for a in rows if a.outcome == "fatal")
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_flagged": len(rows),
        "fatal_outcomes": fatal,
        "confirmed_saes": sum(1 for a in rows if a.sae_confirmed),
        "by_system_organ_class": by_soc,
        "dictionary": meddra.DICTIONARY_NAME,
    }


@router.get("/stats")
def ae_stats(study_id: str | None = None, db: Session = Depends(get_db),
             user: User = Depends(get_current_user)):
    scoped = accessible_study_ids(db, user)

    def grouped(column):
        stmt = select(column, func.count()).group_by(column)
        if scoped is not None:
            stmt = stmt.where(AdverseEvent.study_id.in_(scoped or ["__none__"]))
        if study_id:
            stmt = stmt.where(AdverseEvent.study_id == study_id)
        return {str(k): v for k, v in db.execute(stmt).all()}

    return {
        "by_seriousness": grouped(AdverseEvent.seriousness),
        "by_severity": grouped(AdverseEvent.severity),
        "by_status": grouped(AdverseEvent.status),
        "by_outcome": grouped(AdverseEvent.outcome),
        "by_system_organ_class": grouped(AdverseEvent.meddra_soc),
        "by_causality": grouped(AdverseEvent.causality),
    }


@router.get("/{ae_id}")
def get_ae(ae_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _with_deadline(_get_ae(db, user, ae_id))


@router.put("/{ae_id}")
def update_ae(ae_id: str, payload: AeUpdate, ctx: AuditContext = Depends(audit_ctx),
              user: User = Depends(CAPTURE_ROLES)):
    db = ctx.db
    ae = _get_ae(db, user, ae_id)
    assert_not_locked(ae.study)
    before = AeOut.model_validate(ae).model_dump()
    updates = payload.model_dump(exclude_unset=True)
    for f, v in updates.items():
        setattr(ae, f, v)
    # seriousness / outcome changes recalculate the statutory deadline
    if "seriousness" in updates or "outcome" in updates or "seriousness_criteria" in updates:
        d = compute_deadlines(
            seriousness=ae.seriousness,
            outcome=ae.outcome,
            seriousness_criteria=ae.seriousness_criteria,
            report_date=ae.report_date,
        )
        ae.regulatory_deadline = d["regulatory_deadline"]
        ae.followup_deadline = d["followup_deadline"]
        if ae.seriousness == "serious":
            ae.dsmb_flag = True
    db.commit()
    db.refresh(ae)
    ctx.log("UPDATE", "AdverseEvent", ae.id, old=before,
            new=AeOut.model_validate(ae).model_dump())
    return _with_deadline(ae)


@router.post("/{ae_id}/code")
def code_ae(ae_id: str, payload: AeCodeRequest, ctx: AuditContext = Depends(audit_ctx),
            user: User = Depends(PV_ROLES)):
    """Assign a coded term (synthetic MedDRA stub) to an AE."""
    db = ctx.db
    ae = _get_ae(db, user, ae_id)
    hit = None
    if payload.meddra_code:
        hit = meddra.by_code(payload.meddra_code)
        if hit is None:
            raise HTTPException(
                status_code=400,
                detail=f"Code '{payload.meddra_code}' not found in {meddra.DICTIONARY_NAME}",
            )
    elif payload.term:
        hit = meddra.autocode(payload.term)
        if hit is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"No confident match for '{payload.term}'. Use "
                    "/api/ae/dictionary/search and pass an explicit meddra_code."
                ),
            )
    else:
        raise HTTPException(status_code=400, detail="Provide either meddra_code or term")

    before = {
        "meddra_code": ae.meddra_code,
        "meddra_pt": ae.meddra_pt,
        "meddra_soc": ae.meddra_soc,
        "status": ae.status,
    }
    ae.meddra_code = hit["code"]
    ae.meddra_pt = hit["pt"]
    ae.meddra_soc = hit["soc"]
    ae.coding_dictionary = meddra.DICTIONARY_NAME
    ae.coded_by = user.id
    ae.coded_at = datetime.now(timezone.utc)
    if ae.status == "open":
        ae.status = "coded"
    db.commit()
    db.refresh(ae)
    ctx.log("CODE_AE", "AdverseEvent", ae.id, old=before,
            new={"meddra_code": ae.meddra_code, "meddra_pt": ae.meddra_pt,
                 "meddra_soc": ae.meddra_soc, "status": ae.status},
            reason=f"Coded with {meddra.DICTIONARY_NAME}")
    return _with_deadline(ae)


@router.post("/{ae_id}/confirm-sae")
def confirm_sae(ae_id: str, payload: SaeConfirmRequest,
                ctx: AuditContext = Depends(audit_ctx), user: User = Depends(PV_ROLES)):
    db = ctx.db
    ae = _get_ae(db, user, ae_id)
    if ae.seriousness != "serious":
        raise HTTPException(
            status_code=409,
            detail="Only events recorded as 'serious' can be SAE-confirmed.",
        )
    before = AeOut.model_validate(ae).model_dump()
    now = datetime.now(timezone.utc)
    ae.sae_confirmed = payload.confirmed
    ae.sae_confirmed_by = user.id
    ae.sae_confirmed_at = now
    ae.status = "under_review"
    ae.npvcc_reference = payload.npvcc_reference
    if payload.escalate_to_ec:
        ae.ec_escalated = True
        ae.ec_escalated_at = now
        db.add(
            Alert(
                study_id=ae.study_id,
                alert_type="sae_escalated_to_ec",
                severity="critical",
                message=f"SAE {ae.ae_number} confirmed and escalated to the Ethics Committee.",
                entity_type="AdverseEvent",
                entity_id=ae.id,
                target_roles=["ec", "admin", "pi"],
            )
        )
    if payload.flag_for_dsmb:
        ae.dsmb_flag = True
    db.commit()
    db.refresh(ae)
    ctx.log("CONFIRM_SAE", "AdverseEvent", ae.id, old=before,
            new=AeOut.model_validate(ae).model_dump(), reason=payload.remarks)
    return _with_deadline(ae)


@router.post("/{ae_id}/ec-acknowledge")
def ec_acknowledge(ae_id: str, remarks: str | None = None,
                   ctx: AuditContext = Depends(audit_ctx),
                   user: User = Depends(RequireRoles("ec", "admin"))):
    db = ctx.db
    ae = _get_ae(db, user, ae_id)
    if not ae.ec_escalated:
        raise HTTPException(status_code=409, detail="This event has not been escalated to the EC")
    old = {"ec_acknowledged": ae.ec_acknowledged}
    ae.ec_acknowledged = True
    db.commit()
    db.refresh(ae)
    ctx.log("EC_ACKNOWLEDGE_SAE", "AdverseEvent", ae.id, old=old,
            new={"ec_acknowledged": True}, reason=remarks)
    return _with_deadline(ae)


@router.post("/{ae_id}/mark-reported")
def mark_reported(ae_id: str, payload: AeReportedRequest,
                  ctx: AuditContext = Depends(audit_ctx), user: User = Depends(PV_ROLES)):
    """Record that the event was submitted to the regulatory authority."""
    db = ctx.db
    ae = _get_ae(db, user, ae_id)
    before = AeOut.model_validate(ae).model_dump()
    reported_at = payload.reported_at or datetime.now(timezone.utc)
    ae.reported_to_authority_at = reported_at
    ae.status = "reported"
    if payload.reference:
        ae.npvcc_reference = payload.reference
    db.commit()
    db.refresh(ae)
    dl = ae.regulatory_deadline
    if dl is not None and dl.tzinfo is None:
        dl = dl.replace(tzinfo=timezone.utc)
    within = dl is None or reported_at <= dl
    ctx.log("MARK_REPORTED", "AdverseEvent", ae.id, old=before,
            new=AeOut.model_validate(ae).model_dump(),
            reason=(
                f"Reported to {payload.authority} at {reported_at.isoformat()} - "
                f"{'WITHIN' if within else 'AFTER'} the statutory deadline"
            ))
    out = _with_deadline(ae)
    out["reported_within_deadline"] = within
    return out


@router.post("/{ae_id}/resolve")
def resolve_ae(ae_id: str, resolution_date: date | None = None,
               outcome: str = "recovered",
               ctx: AuditContext = Depends(audit_ctx),
               user: User = Depends(CAPTURE_ROLES)):
    db = ctx.db
    ae = _get_ae(db, user, ae_id)
    before = AeOut.model_validate(ae).model_dump()
    ae.resolution_date = resolution_date or date.today()
    ae.outcome = outcome
    ae.status = "resolved"
    db.commit()
    db.refresh(ae)
    ctx.log("RESOLVE_AE", "AdverseEvent", ae.id, old=before,
            new=AeOut.model_validate(ae).model_dump())
    return _with_deadline(ae)
