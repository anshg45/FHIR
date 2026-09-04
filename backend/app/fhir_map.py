"""FHIR R4 resource mappers + Bundle / OperationOutcome helpers.

Every mapper emits FHIR R4 compliant JSON (validator.fhir.org compatible).
CTMS internal codes are translated to the official FHIR value sets; the raw
internal code is always preserved in an extension so nothing is lost.
"""
from datetime import date, datetime, timezone
from typing import Any

EXT_BASE = "http://aiia.gov.in/fhir/StructureDefinition"
DATA_ABSENT = "http://hl7.org/fhir/StructureDefinition/data-absent-reason"

# ------------------------------------------------------------- code mappings
STUDY_STATUS_MAP = {
    "draft": "in-review",
    "protocol_submitted": "in-review",
    "ec_approved": "approved",
    "ec_rejected": "disapproved",
    "ctri_registered": "approved",
    "active": "active",
    "suspended": "temporarily-closed-to-accrual",
    "completed": "completed",
    "terminated": "withdrawn",
}

PHASE_MAP = {
    "phase 1": ("phase-1", "Phase 1"),
    "phase 2": ("phase-2", "Phase 2"),
    "phase 2/3": ("phase-2-phase-3", "Phase 2/Phase 3"),
    "phase 3": ("phase-3", "Phase 3"),
    "phase 4": ("phase-4", "Phase 4"),
    "n/a": ("n-a", "N/A"),
}

SUBJECT_STATUS_MAP = {
    "screened": "screening",
    "enrolled": "on-study",
    "completed": "off-study",
    "withdrawn": "withdrawn",
    "screen_failed": "ineligible",
}

ENCOUNTER_STATUS_MAP = {
    "scheduled": "planned",
    "completed": "finished",
    "missed": "cancelled",
    "overdue": "planned",
}

AE_OUTCOME_MAP = {
    "recovered": ("resolved", "Resolved"),
    "recovering": ("recovering", "Recovering"),
    "ongoing": ("ongoing", "Ongoing"),
    "recovered_with_sequelae": ("resolvedWithSequelae", "Resolved with Sequelae"),
    "fatal": ("fatal", "Fatal"),
    "unknown": ("unknown", "Unknown"),
}

GENDER_MAP = {"male": "male", "female": "female", "other": "other", "unknown": "unknown",
              "m": "male", "f": "female"}


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _meta(obj) -> dict:
    last = getattr(obj, "updated_at", None) or getattr(obj, "created_at", None)
    meta = {"versionId": "1"}
    if last:
        meta["lastUpdated"] = _iso(last)
    return meta


def _cc(system: str, code: str, display: str | None = None, text: str | None = None) -> dict:
    coding = {"system": system, "code": code}
    if display:
        coding["display"] = display
    out = {"coding": [coding]}
    if text:
        out["text"] = text
    return out


def _ext_string(url: str, value: Any) -> dict:
    return {"url": f"{EXT_BASE}/{url}", "valueString": str(value)}


# ------------------------------------------------------------ ResearchStudy
def _study_identifiers(s) -> list[dict]:
    identifiers = [
        {
            "use": "official",
            "system": "http://aiia.gov.in/ctms/protocol-number",
            "value": s.protocol_number,
        }
    ]
    if s.ctri_registration_number:
        identifiers.append(
            {
                "use": "secondary",
                "type": _cc("http://terminology.hl7.org/CodeSystem/v2-0203", "PLAC",
                            "Placer Identifier", "CTRI Registration Number"),
                "system": "http://ctri.nic.in",
                "value": s.ctri_registration_number,
            }
        )
    if s.iec_approval_number:
        identifiers.append(
            {
                "use": "secondary",
                "system": "http://aiia.gov.in/iec/approval-number",
                "value": s.iec_approval_number,
            }
        )
    return identifiers


def _study_period(s) -> dict | None:
    if not (s.start_date or s.end_date):
        return None
    period = {}
    if s.start_date:
        period["start"] = _iso(s.start_date)
    if s.end_date:
        period["end"] = _iso(s.end_date)
    return period


def _study_extensions(s) -> list[dict]:
    ext = [
        _ext_string("ctms-status", s.status),
        _ext_string("ctri-status", s.ctri_status),
        _ext_string("iec-approval-status", s.iec_approval_status),
        {"url": f"{EXT_BASE}/enrollment-target", "valueInteger": s.enrollment_target or 0},
        {"url": f"{EXT_BASE}/enrolled-count", "valueInteger": s.enrolled_count or 0},
    ]
    if s.iec_renewal_due:
        ext.append(
            {"url": f"{EXT_BASE}/iec-renewal-due", "valueDate": _iso(s.iec_renewal_due)}
        )
    return ext


def research_study_to_fhir(s) -> dict:
    res: dict = {
        "resourceType": "ResearchStudy",
        "id": s.id,
        "meta": _meta(s),
        "identifier": _study_identifiers(s),
        "title": s.title,
        "status": STUDY_STATUS_MAP.get(s.status, "in-review"),
        "primaryPurposeType": _cc(
            "http://terminology.hl7.org/CodeSystem/research-study-prim-purp-type",
            "treatment",
            "Treatment",
        ),
    }
    if s.phase:
        code, display = PHASE_MAP.get(s.phase.strip().lower(), ("n-a", "N/A"))
        res["phase"] = _cc(
            "http://terminology.hl7.org/CodeSystem/research-study-phase", code, display, s.phase
        )
    if s.therapeutic_area:
        res["category"] = [{"text": s.therapeutic_area}]
    if s.condition:
        res["condition"] = [{"text": s.condition}]
    if s.description:
        res["description"] = s.description
    if s.sponsor:
        res["sponsor"] = {"display": s.sponsor}
    if s.principal_investigator_id:
        res["principalInvestigator"] = {
            "reference": f"Practitioner/{s.principal_investigator_id}",
            "display": getattr(s.principal_investigator, "name", None),
        }
    if s.site_id:
        res["site"] = [
            {"reference": f"Location/{s.site_id}", "display": getattr(s.site, "name", None)}
        ]
    period = _study_period(s)
    if period:
        res["period"] = period
    if s.intervention:
        res["focus"] = [{"text": s.intervention}]
    res["extension"] = _study_extensions(s)
    return res


# ------------------------------------------------------------------- Patient
def patient_to_fhir(p) -> dict:
    res: dict = {
        "resourceType": "Patient",
        "id": p.id,
        "meta": _meta(p),
        "identifier": [
            {
                "use": "official",
                "system": "http://aiia.gov.in/ctms/screening-number",
                "value": p.screening_number,
            }
        ],
        "active": p.status in ("screened", "enrolled"),
        "gender": GENDER_MAP.get((p.sex or "unknown").lower(), "unknown"),
    }
    if p.randomization_number:
        res["identifier"].append(
            {
                "use": "secondary",
                "system": "http://aiia.gov.in/ctms/randomization-number",
                "value": p.randomization_number,
            }
        )
    # Trial subjects are pseudonymised: exact DOB is deliberately not stored
    # (data minimisation). Age is carried in an extension.
    res["_birthDate"] = {
        "extension": [
            {
                "url": DATA_ABSENT,
                "valueCode": "masked",
            }
        ]
    }
    res["extension"] = [
        {"url": f"{EXT_BASE}/age-years", "valueInteger": p.age or 0},
        _ext_string("subject-status", p.status),
        {"url": f"{EXT_BASE}/consent-obtained", "valueBoolean": bool(p.consent_obtained)},
    ]
    if p.consent_date:
        res["extension"].append(
            {"url": f"{EXT_BASE}/consent-date", "valueDate": _iso(p.consent_date)}
        )
    if p.site_id:
        res["managingOrganization"] = {"reference": f"Organization/{p.site_id}"}
    return res


# ----------------------------------------------------------- ResearchSubject
def research_subject_to_fhir(p) -> dict:
    res: dict = {
        "resourceType": "ResearchSubject",
        "id": p.id,
        "meta": _meta(p),
        "identifier": [
            {
                "use": "official",
                "system": "http://aiia.gov.in/ctms/screening-number",
                "value": p.screening_number,
            }
        ],
        "status": SUBJECT_STATUS_MAP.get(p.status, "candidate"),
        "study": {"reference": f"ResearchStudy/{p.study_id}"},
        "individual": {"reference": f"Patient/{p.id}"},
    }
    if p.enrollment_date or p.completion_date:
        period = {}
        if p.enrollment_date:
            period["start"] = _iso(p.enrollment_date)
        if p.completion_date:
            period["end"] = _iso(p.completion_date)
        res["period"] = period
    if p.arm:
        res["actualArm"] = p.arm
    if p.consent_obtained:
        res["extension"] = [
            _ext_string("consent-version", p.consent_version or "unspecified"),
        ]
    return res


# ------------------------------------------------------------- AdverseEvent
def _ae_event(ae) -> dict:
    if ae.meddra_code:
        return {
            "coding": [
                {
                    "system": "http://aiia.gov.in/fhir/CodeSystem/synthetic-meddra",
                    "code": ae.meddra_code,
                    "display": ae.meddra_pt or ae.ae_term,
                }
            ],
            "text": ae.ae_term or ae.description,
        }
    return {"text": ae.ae_term or ae.description}


def _ae_seriousness(ae) -> dict:
    return _cc(
        "http://terminology.hl7.org/CodeSystem/adverse-event-seriousness",
        "serious" if ae.seriousness == "serious" else "non-serious",
        "Serious" if ae.seriousness == "serious" else "Non-serious",
        ae.seriousness_criteria,
    )


def _ae_extensions(ae) -> list[dict]:
    ext = [
        _ext_string("ae-workflow-status", ae.status),
        {"url": f"{EXT_BASE}/sae-confirmed", "valueBoolean": bool(ae.sae_confirmed)},
        {"url": f"{EXT_BASE}/ec-escalated", "valueBoolean": bool(ae.ec_escalated)},
        {"url": f"{EXT_BASE}/dsmb-flag", "valueBoolean": bool(ae.dsmb_flag)},
    ]
    if ae.regulatory_deadline:
        ext.append(
            {
                "url": f"{EXT_BASE}/regulatory-deadline",
                "valueDateTime": _iso(ae.regulatory_deadline),
            }
        )
    if ae.causality:
        ext.append(_ext_string("causality-assessment", ae.causality))
    if ae.meddra_soc:
        ext.append(_ext_string("meddra-soc", ae.meddra_soc))
    return ext


def adverse_event_to_fhir(ae) -> dict:
    res: dict = {
        "resourceType": "AdverseEvent",
        "id": ae.id,
        "meta": _meta(ae),
        "identifier": {
            "system": "http://aiia.gov.in/ctms/ae-number",
            "value": ae.ae_number,
        },
        "actuality": "actual",
        "category": [
            _cc(
                "http://terminology.hl7.org/CodeSystem/adverse-event-category",
                "medication-mishap",
                "Medication Mishap",
            )
        ],
        "subject": {"reference": f"Patient/{ae.patient_id}"},
        "event": _ae_event(ae),
        "seriousness": _ae_seriousness(ae),
    }
    if ae.onset_date:
        res["date"] = _iso(ae.onset_date)
        res["detected"] = _iso(ae.onset_date)
    if ae.report_date:
        res["recordedDate"] = _iso(ae.report_date)
    if ae.severity:
        res["severity"] = _cc(
            "http://terminology.hl7.org/CodeSystem/adverse-event-severity",
            ae.severity,
            ae.severity.capitalize(),
        )
    if ae.outcome:
        code, display = AE_OUTCOME_MAP.get(ae.outcome, ("unknown", "Unknown"))
        res["outcome"] = _cc(
            "http://terminology.hl7.org/CodeSystem/adverse-event-outcome", code, display
        )
    if ae.reported_by:
        res["recorder"] = {"reference": f"Practitioner/{ae.reported_by}"}
    res["study"] = [{"reference": f"ResearchStudy/{ae.study_id}"}]
    if ae.suspect_intervention:
        res["suspectEntity"] = [{"instance": {"display": ae.suspect_intervention}}]
    if ae.narrative:
        res["note"] = [{"text": ae.narrative}]
    res["extension"] = _ae_extensions(ae)
    return res


# ----------------------------------------------------------------- Encounter
def visit_to_fhir(v) -> dict:
    res: dict = {
        "resourceType": "Encounter",
        "id": v.id,
        "meta": _meta(v),
        "identifier": [
            {
                "system": "http://aiia.gov.in/ctms/visit",
                "value": f"{v.study_id}:{v.patient_id}:V{v.visit_number}",
            }
        ],
        "status": ENCOUNTER_STATUS_MAP.get(v.status, "unknown"),
        "class": {
            "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
            "code": "AMB",
            "display": "ambulatory",
        },
        "type": [{"text": v.visit_name or f"Study Visit {v.visit_number}"}],
        "subject": {"reference": f"Patient/{v.patient_id}"},
    }
    actual = v.actual_date or v.scheduled_date
    if actual:
        res["period"] = {"start": _iso(actual)}
    res["extension"] = [
        {"url": f"{EXT_BASE}/visit-number", "valueInteger": v.visit_number},
        _ext_string("ctms-visit-status", v.status),
        {"url": f"{EXT_BASE}/deviation-flag", "valueBoolean": bool(v.deviation_flag)},
        _ext_string("research-study", f"ResearchStudy/{v.study_id}"),
    ]
    if v.scheduled_date:
        res["extension"].append(
            {"url": f"{EXT_BASE}/scheduled-date", "valueDate": _iso(v.scheduled_date)}
        )
    return res


# -------------------------------------------------------- Bundle / outcomes
def make_bundle(
    resources: list[dict],
    *,
    total: int,
    base_url: str,
    self_link: str,
    bundle_type: str = "searchset",
) -> dict:
    return {
        "resourceType": "Bundle",
        "id": f"searchset-{int(datetime.now(timezone.utc).timestamp() * 1000)}",
        "meta": {"lastUpdated": _iso(datetime.now(timezone.utc))},
        "type": bundle_type,
        "total": total,
        "link": [{"relation": "self", "url": self_link}],
        "entry": [
            {
                "fullUrl": f"{base_url}/{r['resourceType']}/{r['id']}",
                "resource": r,
                "search": {"mode": "match"},
            }
            for r in resources
        ],
    }


def operation_outcome(
    severity: str, code: str, diagnostics: str, *, expression: list[str] | None = None
) -> dict:
    issue: dict = {"severity": severity, "code": code, "diagnostics": diagnostics}
    if expression:
        issue["expression"] = expression
    return {"resourceType": "OperationOutcome", "issue": [issue]}


# ------------------------------------------------------- CapabilityStatement
RESOURCE_SEARCH_PARAMS = {
    "ResearchStudy": [
        ("_id", "token"),
        ("identifier", "token"),
        ("status", "token"),
        ("title", "string"),
        ("site", "reference"),
        ("principalinvestigator", "reference"),
        ("date", "date"),
    ],
    "Patient": [
        ("_id", "token"),
        ("identifier", "token"),
        ("gender", "token"),
        ("active", "token"),
    ],
    "ResearchSubject": [
        ("_id", "token"),
        ("identifier", "token"),
        ("status", "token"),
        ("study", "reference"),
        ("individual", "reference"),
        ("patient", "reference"),
    ],
    "AdverseEvent": [
        ("_id", "token"),
        ("identifier", "token"),
        ("actuality", "token"),
        ("seriousness", "token"),
        ("severity", "token"),
        ("subject", "reference"),
        ("study", "reference"),
        ("date", "date"),
        ("recorder", "reference"),
    ],
    "Encounter": [
        ("_id", "token"),
        ("identifier", "token"),
        ("status", "token"),
        ("subject", "reference"),
        ("patient", "reference"),
        ("date", "date"),
    ],
}


def capability_statement(base_url: str, version: str) -> dict:
    return {
        "resourceType": "CapabilityStatement",
        "id": "aiia-ctms-fhir-capability",
        "url": f"{base_url}/metadata",
        "version": version,
        "name": "AIIACTMSFHIRAPI",
        "title": "AIIA CTMS FHIR R4 Read/Search API",
        "status": "active",
        "experimental": True,
        "date": _iso(datetime.now(timezone.utc)),
        "publisher": "All India Institute of Ayurveda (AIIA)",
        "contact": [
            {"telecom": [{"system": "url", "value": "https://aiia.gov.in"}]}
        ],
        "description": (
            "FHIR R4 aligned read/search interface over the AIIA Clinical Trial "
            "Management System. Designed for ABDM sandbox interoperability. "
            "Read-only: no create/update/delete is exposed on the FHIR surface."
        ),
        "kind": "instance",
        "software": {"name": "AIIA CTMS Backend", "version": version},
        "implementation": {"description": "AIIA CTMS FHIR endpoint", "url": base_url},
        "fhirVersion": "4.0.1",
        "format": ["json", "application/fhir+json"],
        "rest": [
            {
                "mode": "server",
                "documentation": "Bearer JWT authentication is required on all FHIR endpoints.",
                "security": {
                    "cors": True,
                    "service": [
                        _cc(
                            "http://terminology.hl7.org/CodeSystem/restful-security-service",
                            "OAuth",
                            "OAuth",
                        )
                    ],
                    "description": "JWT bearer token issued by /api/auth/login",
                },
                "resource": [
                    {
                        "type": rtype,
                        "profile": f"http://hl7.org/fhir/StructureDefinition/{rtype}",
                        "interaction": [
                            {"code": "read"},
                            {"code": "search-type"},
                        ],
                        "searchParam": [
                            {"name": name, "type": ptype} for name, ptype in params
                        ],
                    }
                    for rtype, params in RESOURCE_SEARCH_PARAMS.items()
                ],
                "interaction": [{"code": "search-system"}],
            }
        ],
    }
