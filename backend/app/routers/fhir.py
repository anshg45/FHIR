"""FHIR R4 read/search API surface.

Supports: ResearchStudy, Patient, ResearchSubject, AdverseEvent, Encounter
         + CapabilityStatement + Bundles + OperationOutcome errors.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..fhir_map import (
    adverse_event_to_fhir,
    capability_statement,
    make_bundle,
    operation_outcome,
    patient_to_fhir,
    research_study_to_fhir,
    research_subject_to_fhir,
    visit_to_fhir,
)
from ..models import AdverseEvent, Patient, ResearchStudy, User, VisitLog
from ..security import get_current_user

router = APIRouter(prefix="/fhir", tags=["FHIR R4"])

FHIR_MEDIA = "application/fhir+json"


def _fhir_response(payload: dict, status_code: int = 200) -> JSONResponse:
    return JSONResponse(content=payload, status_code=status_code,
                        media_type=FHIR_MEDIA)


def _base_url(request: Request) -> str:
    # Preserve /api/fhir prefix
    return str(request.url).split("?")[0].rsplit("/", 1)[0] if "/" in str(request.url) else settings.FHIR_BASE_URL


def _fhir_base(request: Request) -> str:
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host") or request.url.netloc
    return f"{scheme}://{host}/api/fhir"


# ---------------------------------------------------------- CapabilityStatement
@router.get("/metadata")
def metadata(request: Request, _: User = Depends(get_current_user)):
    base = _fhir_base(request)
    return _fhir_response(capability_statement(base, settings.APP_VERSION))


# ------------------------------------------------------------ ResearchStudy
@router.get("/ResearchStudy/{study_id}")
def read_research_study(study_id: str, request: Request,
                        db: Session = Depends(get_db),
                        _: User = Depends(get_current_user)):
    s = db.get(ResearchStudy, study_id)
    if s is None:
        return _fhir_response(
            operation_outcome("error", "not-found",
                              f"ResearchStudy/{study_id} not found"), 404)
    return _fhir_response(research_study_to_fhir(s))


@router.get("/ResearchStudy")
def search_research_studies(
    request: Request,
    _id: str | None = None,
    identifier: str | None = None,
    status: str | None = None,
    title: str | None = None,
    site: str | None = None,
    _count: int = Query(50, le=500, alias="_count"),
    _offset: int = Query(0, alias="_offset"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    stmt = select(ResearchStudy)
    if _id:
        stmt = stmt.where(ResearchStudy.id == _id)
    if identifier:
        stmt = stmt.where(
            (ResearchStudy.protocol_number == identifier)
            | (ResearchStudy.ctri_registration_number == identifier)
            | (ResearchStudy.iec_approval_number == identifier)
        )
    if status:
        # accept either internal or FHIR status codes
        stmt = stmt.where(ResearchStudy.status == status)
    if title:
        stmt = stmt.where(func.lower(ResearchStudy.title).like(f"%{title.lower()}%"))
    if site:
        stmt = stmt.where(ResearchStudy.site_id == site.replace("Location/", ""))

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = (
        db.execute(stmt.order_by(ResearchStudy.created_at.desc())
                       .limit(_count).offset(_offset))
        .scalars().all()
    )
    base = _fhir_base(request)
    bundle = make_bundle(
        [research_study_to_fhir(s) for s in rows],
        total=total, base_url=base,
        self_link=str(request.url),
    )
    return _fhir_response(bundle)


# ------------------------------------------------------------------- Patient
@router.get("/Patient/{patient_id}")
def read_patient(patient_id: str, db: Session = Depends(get_db),
                 _: User = Depends(get_current_user)):
    p = db.get(Patient, patient_id)
    if p is None:
        return _fhir_response(
            operation_outcome("error", "not-found", f"Patient/{patient_id} not found"), 404)
    return _fhir_response(patient_to_fhir(p))


@router.get("/Patient")
def search_patients(
    request: Request,
    _id: str | None = None,
    identifier: str | None = None,
    gender: str | None = None,
    active: str | None = None,
    _count: int = Query(50, le=500, alias="_count"),
    _offset: int = Query(0, alias="_offset"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    stmt = select(Patient)
    if _id:
        stmt = stmt.where(Patient.id == _id)
    if identifier:
        stmt = stmt.where(
            (Patient.screening_number == identifier)
            | (Patient.randomization_number == identifier)
        )
    if gender:
        stmt = stmt.where(Patient.sex == gender)
    if active is not None:
        is_active = active.lower() in ("true", "1")
        if is_active:
            stmt = stmt.where(Patient.status.in_(["screened", "enrolled"]))
        else:
            stmt = stmt.where(Patient.status.notin_(["screened", "enrolled"]))
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(stmt.limit(_count).offset(_offset)).scalars().all()
    return _fhir_response(make_bundle(
        [patient_to_fhir(p) for p in rows],
        total=total, base_url=_fhir_base(request),
        self_link=str(request.url),
    ))


# --------------------------------------------------------- ResearchSubject
@router.get("/ResearchSubject/{subject_id}")
def read_research_subject(subject_id: str, db: Session = Depends(get_db),
                          _: User = Depends(get_current_user)):
    p = db.get(Patient, subject_id)
    if p is None:
        return _fhir_response(
            operation_outcome("error", "not-found",
                              f"ResearchSubject/{subject_id} not found"), 404)
    return _fhir_response(research_subject_to_fhir(p))


@router.get("/ResearchSubject")
def search_research_subjects(
    request: Request,
    _id: str | None = None,
    identifier: str | None = None,
    status: str | None = None,
    study: str | None = None,
    individual: str | None = None,
    patient: str | None = None,
    _count: int = Query(50, le=500, alias="_count"),
    _offset: int = Query(0, alias="_offset"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    stmt = select(Patient)
    if _id:
        stmt = stmt.where(Patient.id == _id)
    if identifier:
        stmt = stmt.where(Patient.screening_number == identifier)
    if status:
        # translate FHIR to internal
        rev = {"screening": "screened", "on-study": "enrolled",
               "off-study": "completed", "withdrawn": "withdrawn",
               "ineligible": "screen_failed"}
        stmt = stmt.where(Patient.status == rev.get(status, status))
    if study:
        stmt = stmt.where(Patient.study_id == study.replace("ResearchStudy/", ""))
    ref = individual or patient
    if ref:
        stmt = stmt.where(Patient.id == ref.replace("Patient/", ""))
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(stmt.limit(_count).offset(_offset)).scalars().all()
    return _fhir_response(make_bundle(
        [research_subject_to_fhir(p) for p in rows],
        total=total, base_url=_fhir_base(request),
        self_link=str(request.url),
    ))


# ----------------------------------------------------------- AdverseEvent
@router.get("/AdverseEvent/{ae_id}")
def read_adverse_event(ae_id: str, db: Session = Depends(get_db),
                       _: User = Depends(get_current_user)):
    ae = db.get(AdverseEvent, ae_id)
    if ae is None:
        return _fhir_response(
            operation_outcome("error", "not-found", f"AdverseEvent/{ae_id} not found"), 404)
    return _fhir_response(adverse_event_to_fhir(ae))


@router.get("/AdverseEvent")
def search_adverse_events(
    request: Request,
    _id: str | None = None,
    identifier: str | None = None,
    actuality: str | None = None,
    seriousness: str | None = None,
    severity: str | None = None,
    subject: str | None = None,
    patient: str | None = None,
    study: str | None = None,
    _count: int = Query(50, le=500, alias="_count"),
    _offset: int = Query(0, alias="_offset"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    stmt = select(AdverseEvent)
    if _id:
        stmt = stmt.where(AdverseEvent.id == _id)
    if identifier:
        stmt = stmt.where(AdverseEvent.ae_number == identifier)
    if seriousness:
        internal = "serious" if seriousness == "serious" else "non_serious"
        stmt = stmt.where(AdverseEvent.seriousness == internal)
    if severity:
        stmt = stmt.where(AdverseEvent.severity == severity)
    ref = subject or patient
    if ref:
        stmt = stmt.where(AdverseEvent.patient_id == ref.replace("Patient/", ""))
    if study:
        stmt = stmt.where(AdverseEvent.study_id == study.replace("ResearchStudy/", ""))
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(
        stmt.order_by(AdverseEvent.report_date.desc().nullslast())
        .limit(_count).offset(_offset)
    ).scalars().all()
    return _fhir_response(make_bundle(
        [adverse_event_to_fhir(a) for a in rows],
        total=total, base_url=_fhir_base(request),
        self_link=str(request.url),
    ))


# ------------------------------------------------------------- Encounter
@router.get("/Encounter/{encounter_id}")
def read_encounter(encounter_id: str, db: Session = Depends(get_db),
                   _: User = Depends(get_current_user)):
    v = db.get(VisitLog, encounter_id)
    if v is None:
        return _fhir_response(
            operation_outcome("error", "not-found",
                              f"Encounter/{encounter_id} not found"), 404)
    return _fhir_response(visit_to_fhir(v))


@router.get("/Encounter")
def search_encounters(
    request: Request,
    _id: str | None = None,
    status: str | None = None,
    subject: str | None = None,
    patient: str | None = None,
    _count: int = Query(50, le=500, alias="_count"),
    _offset: int = Query(0, alias="_offset"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    stmt = select(VisitLog)
    if _id:
        stmt = stmt.where(VisitLog.id == _id)
    if status:
        rev = {"planned": "scheduled", "finished": "completed",
               "cancelled": "missed"}
        stmt = stmt.where(VisitLog.status == rev.get(status, status))
    ref = subject or patient
    if ref:
        stmt = stmt.where(VisitLog.patient_id == ref.replace("Patient/", ""))
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(stmt.order_by(VisitLog.scheduled_date.desc()).limit(_count).offset(_offset)).scalars().all()
    return _fhir_response(make_bundle(
        [visit_to_fhir(v) for v in rows],
        total=total, base_url=_fhir_base(request),
        self_link=str(request.url),
    ))


# --------------------------------------------------------- everything bundle
@router.get("/ResearchStudy/{study_id}/$everything")
def study_everything(study_id: str, request: Request,
                     db: Session = Depends(get_db),
                     _: User = Depends(get_current_user)):
    s = db.get(ResearchStudy, study_id)
    if s is None:
        return _fhir_response(
            operation_outcome("error", "not-found", f"ResearchStudy/{study_id} not found"), 404)
    patients = db.execute(select(Patient).where(Patient.study_id == s.id)).scalars().all()
    aes = db.execute(select(AdverseEvent).where(AdverseEvent.study_id == s.id)).scalars().all()
    visits = db.execute(select(VisitLog).where(VisitLog.study_id == s.id)).scalars().all()
    resources = [research_study_to_fhir(s)]
    resources += [patient_to_fhir(p) for p in patients]
    resources += [research_subject_to_fhir(p) for p in patients]
    resources += [adverse_event_to_fhir(a) for a in aes]
    resources += [visit_to_fhir(v) for v in visits]
    return _fhir_response(make_bundle(
        resources, total=len(resources),
        base_url=_fhir_base(request),
        self_link=str(request.url),
        bundle_type="collection",
    ))
