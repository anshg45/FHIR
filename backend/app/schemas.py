"""Pydantic v2 request/response schemas."""
from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field

ORM = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------- auth
class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


# -------------------------------------------------------------------- users
RoleLiteral = Literal["pi", "coordinator", "monitor", "ec", "pv", "admin", "regulator"]


class UserCreate(BaseModel):
    name: str
    email: EmailStr
    password: str = Field(min_length=8)
    role: RoleLiteral
    designation: Optional[str] = None
    phone: Optional[str] = None
    site_id: Optional[str] = None


class UserUpdate(BaseModel):
    name: Optional[str] = None
    role: Optional[RoleLiteral] = None
    designation: Optional[str] = None
    phone: Optional[str] = None
    site_id: Optional[str] = None
    is_active: Optional[bool] = None


class UserOut(BaseModel):
    model_config = ORM
    id: str
    name: str
    email: EmailStr
    role: str
    designation: Optional[str] = None
    phone: Optional[str] = None
    site_id: Optional[str] = None
    is_active: bool
    last_login: Optional[datetime] = None
    created_at: Optional[datetime] = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int
    user: UserOut


# -------------------------------------------------------------------- sites
class SiteCreate(BaseModel):
    name: str
    code: str
    city: Optional[str] = None
    state: Optional[str] = None
    country: str = "India"
    contact_person: Optional[str] = None
    contact_email: Optional[EmailStr] = None
    contact_phone: Optional[str] = None


class SiteUpdate(BaseModel):
    name: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    contact_person: Optional[str] = None
    contact_email: Optional[EmailStr] = None
    contact_phone: Optional[str] = None
    is_active: Optional[bool] = None


class SiteOut(BaseModel):
    model_config = ORM
    id: str
    name: str
    code: str
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    contact_person: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    is_active: bool


# ------------------------------------------------------------------ studies
class StudyCreate(BaseModel):
    protocol_number: str
    title: str
    description: Optional[str] = None
    phase: Optional[str] = None
    study_design: Optional[str] = None
    sponsor: Optional[str] = None
    therapeutic_area: Optional[str] = None
    condition: Optional[str] = None
    intervention: Optional[str] = None
    enrollment_target: int = 0
    site_id: Optional[str] = None
    principal_investigator_id: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None


class StudyUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    phase: Optional[str] = None
    study_design: Optional[str] = None
    sponsor: Optional[str] = None
    therapeutic_area: Optional[str] = None
    condition: Optional[str] = None
    intervention: Optional[str] = None
    enrollment_target: Optional[int] = None
    site_id: Optional[str] = None
    principal_investigator_id: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None


class StudyOut(BaseModel):
    model_config = ORM
    id: str
    protocol_number: str
    title: str
    description: Optional[str] = None
    status: str
    phase: Optional[str] = None
    study_design: Optional[str] = None
    sponsor: Optional[str] = None
    therapeutic_area: Optional[str] = None
    condition: Optional[str] = None
    intervention: Optional[str] = None
    ctri_registration_number: Optional[str] = None
    ctri_status: str
    ctri_registration_date: Optional[date] = None
    iec_approval_status: str
    iec_approval_number: Optional[str] = None
    iec_approval_date: Optional[date] = None
    iec_renewal_due: Optional[date] = None
    iec_remarks: Optional[str] = None
    enrollment_target: int
    enrolled_count: int
    site_id: Optional[str] = None
    principal_investigator_id: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    data_locked: bool
    closeout_signed_by: Optional[str] = None
    closeout_signed_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class EcDecisionRequest(BaseModel):
    decision: Literal["approved", "rejected"]
    iec_approval_number: Optional[str] = None
    iec_approval_date: Optional[date] = None
    iec_renewal_due: Optional[date] = None
    remarks: Optional[str] = None


class CtriRegistrationRequest(BaseModel):
    """CTRI has no public write API - registration details are entered manually."""

    ctri_registration_number: str = Field(min_length=5)
    ctri_registration_date: Optional[date] = None
    ctri_status: Literal["submitted", "registered", "rejected"] = "registered"


class StatusChangeRequest(BaseModel):
    target_status: Literal[
        "draft", "protocol_submitted", "ec_approved", "ec_rejected",
        "ctri_registered", "active", "suspended", "completed", "terminated",
    ]
    reason: Optional[str] = None


class CloseoutRequest(BaseModel):
    signed_by: str
    statement: str = "I confirm the trial data are complete and accurate."


class AssignmentRequest(BaseModel):
    user_id: str
    role_on_study: Optional[str] = None


# ----------------------------------------------------------------- patients
class PatientScreen(BaseModel):
    study_id: str
    screening_number: Optional[str] = None
    subject_initials: Optional[str] = None
    screening_date: Optional[date] = None
    age: Optional[int] = Field(default=None, ge=0, le=120)
    sex: Optional[Literal["male", "female", "other", "unknown"]] = None


class PatientEnroll(BaseModel):
    enrollment_date: Optional[date] = None
    randomization_number: Optional[str] = None
    arm: Optional[str] = None
    consent_obtained: bool = True
    consent_date: Optional[date] = None
    consent_version: Optional[str] = None
    consent_language: Optional[str] = None


class PatientUpdate(BaseModel):
    subject_initials: Optional[str] = None
    age: Optional[int] = None
    sex: Optional[str] = None
    arm: Optional[str] = None
    consent_obtained: Optional[bool] = None
    consent_date: Optional[date] = None
    consent_version: Optional[str] = None
    consent_language: Optional[str] = None


class PatientWithdraw(BaseModel):
    withdrawal_reason: str
    date: Optional[date] = None


class ScreenFailRequest(BaseModel):
    screen_failure_reason: str


class PatientOut(BaseModel):
    model_config = ORM
    id: str
    study_id: str
    screening_number: str
    randomization_number: Optional[str] = None
    subject_initials: Optional[str] = None
    screening_date: Optional[date] = None
    enrollment_date: Optional[date] = None
    completion_date: Optional[date] = None
    status: str
    age: Optional[int] = None
    sex: Optional[str] = None
    arm: Optional[str] = None
    consent_obtained: bool
    consent_date: Optional[date] = None
    consent_version: Optional[str] = None
    consent_language: Optional[str] = None
    withdrawal_reason: Optional[str] = None
    screen_failure_reason: Optional[str] = None
    site_id: Optional[str] = None
    created_at: Optional[datetime] = None


# ------------------------------------------------------------------- visits
class VisitCreate(BaseModel):
    study_id: str
    patient_id: str
    visit_number: int
    visit_name: Optional[str] = None
    scheduled_date: Optional[date] = None
    window_days: int = 3


class VisitScheduleBulk(BaseModel):
    patient_id: str
    visit_names: list[str] = ["Baseline", "Week 2", "Week 4", "Week 8", "Week 12"]
    interval_days: int = 14
    first_visit_date: Optional[date] = None


class VisitComplete(BaseModel):
    actual_date: Optional[date] = None
    notes: Optional[str] = None


class VisitUpdate(BaseModel):
    visit_name: Optional[str] = None
    scheduled_date: Optional[date] = None
    actual_date: Optional[date] = None
    status: Optional[Literal["scheduled", "completed", "missed", "overdue"]] = None
    notes: Optional[str] = None


class VisitOut(BaseModel):
    model_config = ORM
    id: str
    study_id: str
    patient_id: str
    visit_number: int
    visit_name: Optional[str] = None
    scheduled_date: Optional[date] = None
    actual_date: Optional[date] = None
    window_days: int
    status: str
    deviation_flag: bool
    notes: Optional[str] = None


# --------------------------------------------------------------- deviations
class DeviationCreate(BaseModel):
    study_id: str
    patient_id: Optional[str] = None
    visit_log_id: Optional[str] = None
    description: str
    category: Optional[str] = None
    severity: Literal["minor", "major", "critical"] = "minor"
    reported_date: Optional[date] = None
    corrective_action: Optional[str] = None


class DeviationReview(BaseModel):
    resolution: Optional[str] = None
    corrective_action: Optional[str] = None
    status: Literal["open", "under_review", "closed"] = "closed"


class DeviationOut(BaseModel):
    model_config = ORM
    id: str
    study_id: str
    patient_id: Optional[str] = None
    visit_log_id: Optional[str] = None
    deviation_number: Optional[str] = None
    description: str
    category: Optional[str] = None
    severity: str
    reported_by: Optional[str] = None
    reported_date: Optional[date] = None
    corrective_action: Optional[str] = None
    resolution: Optional[str] = None
    status: str
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None


# ------------------------------------------------------------- data queries
class QueryCreate(BaseModel):
    study_id: str
    patient_id: Optional[str] = None
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    field_name: Optional[str] = None
    query_text: str
    priority: Literal["low", "medium", "high"] = "medium"


class QueryAnswer(BaseModel):
    response_text: str


class QueryOut(BaseModel):
    model_config = ORM
    id: str
    study_id: str
    patient_id: Optional[str] = None
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    field_name: Optional[str] = None
    query_text: str
    priority: str
    raised_by: Optional[str] = None
    raised_date: Optional[datetime] = None
    response_text: Optional[str] = None
    responded_by: Optional[str] = None
    responded_date: Optional[datetime] = None
    closed_by: Optional[str] = None
    closed_date: Optional[datetime] = None
    status: str


# --------------------------------------------------------------- milestones
class MilestoneCreate(BaseModel):
    study_id: str
    milestone_type: str
    description: Optional[str] = None
    due_date: Optional[date] = None
    owner_role: Optional[str] = None


class MilestoneUpdate(BaseModel):
    milestone_type: Optional[str] = None
    description: Optional[str] = None
    due_date: Optional[date] = None
    completed_date: Optional[date] = None
    status: Optional[Literal["pending", "completed", "overdue"]] = None


class MilestoneOut(BaseModel):
    model_config = ORM
    id: str
    study_id: str
    milestone_type: str
    description: Optional[str] = None
    due_date: Optional[date] = None
    completed_date: Optional[date] = None
    status: str
    owner_role: Optional[str] = None


# ------------------------------------------------------------ adverse events
class AeCreate(BaseModel):
    study_id: str
    patient_id: str
    description: str
    ae_term: Optional[str] = None
    seriousness: Literal["serious", "non_serious"] = "non_serious"
    seriousness_criteria: Optional[str] = None
    severity: Optional[Literal["mild", "moderate", "severe"]] = None
    causality: Optional[
        Literal["certain", "probable", "possible", "unlikely", "unassessable", "not_related"]
    ] = None
    outcome: Optional[
        Literal[
            "recovered", "recovering", "ongoing", "recovered_with_sequelae", "fatal", "unknown"
        ]
    ] = None
    action_taken: Optional[str] = None
    suspect_intervention: Optional[str] = None
    onset_date: Optional[date] = None
    narrative: Optional[str] = None
    auto_code: bool = True


class AeUpdate(BaseModel):
    description: Optional[str] = None
    ae_term: Optional[str] = None
    seriousness: Optional[Literal["serious", "non_serious"]] = None
    seriousness_criteria: Optional[str] = None
    severity: Optional[str] = None
    causality: Optional[str] = None
    outcome: Optional[str] = None
    action_taken: Optional[str] = None
    resolution_date: Optional[date] = None
    narrative: Optional[str] = None
    status: Optional[Literal["open", "under_review", "coded", "resolved", "reported"]] = None


class AeCodeRequest(BaseModel):
    meddra_code: Optional[str] = None
    term: Optional[str] = None


class SaeConfirmRequest(BaseModel):
    confirmed: bool = True
    escalate_to_ec: bool = True
    flag_for_dsmb: bool = True
    npvcc_reference: Optional[str] = None
    remarks: Optional[str] = None


class AeReportedRequest(BaseModel):
    reported_at: Optional[datetime] = None
    authority: str = "CDSCO / NPvCC"
    reference: Optional[str] = None


class AeOut(BaseModel):
    model_config = ORM
    id: str
    ae_number: str
    study_id: str
    patient_id: str
    description: str
    ae_term: Optional[str] = None
    meddra_code: Optional[str] = None
    meddra_pt: Optional[str] = None
    meddra_soc: Optional[str] = None
    coding_dictionary: Optional[str] = None
    coded_by: Optional[str] = None
    coded_at: Optional[datetime] = None
    seriousness: str
    seriousness_criteria: Optional[str] = None
    severity: Optional[str] = None
    causality: Optional[str] = None
    outcome: Optional[str] = None
    action_taken: Optional[str] = None
    suspect_intervention: Optional[str] = None
    onset_date: Optional[date] = None
    resolution_date: Optional[date] = None
    report_date: Optional[datetime] = None
    regulatory_deadline: Optional[datetime] = None
    followup_deadline: Optional[datetime] = None
    reported_to_authority_at: Optional[datetime] = None
    reported_by: Optional[str] = None
    status: str
    sae_confirmed: bool
    ec_escalated: bool
    ec_acknowledged: bool
    dsmb_flag: bool
    npvcc_reference: Optional[str] = None
    narrative: Optional[str] = None
    created_at: Optional[datetime] = None


# ------------------------------------------------------- monitoring reports
class MonitoringReportCreate(BaseModel):
    study_id: str
    visit_date: Optional[date] = None
    visit_type: Optional[str] = "routine"
    findings: Optional[str] = None
    action_items: Optional[str] = None
    subjects_reviewed: int = 0


class MonitoringReportUpdate(BaseModel):
    visit_date: Optional[date] = None
    visit_type: Optional[str] = None
    findings: Optional[str] = None
    action_items: Optional[str] = None
    subjects_reviewed: Optional[int] = None
    status: Optional[Literal["draft", "submitted", "closed"]] = None


class MonitoringReportOut(BaseModel):
    model_config = ORM
    id: str
    study_id: str
    monitor_id: Optional[str] = None
    visit_date: Optional[date] = None
    visit_type: Optional[str] = None
    findings: Optional[str] = None
    action_items: Optional[str] = None
    subjects_reviewed: int
    status: str
    created_at: Optional[datetime] = None


# ------------------------------------------------------------------- alerts
class AlertOut(BaseModel):
    model_config = ORM
    id: str
    study_id: Optional[str] = None
    alert_type: str
    severity: str
    message: str
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    target_roles: Optional[list] = None
    is_read: bool
    resolved: bool
    created_at: Optional[datetime] = None


class ThresholdUpsert(BaseModel):
    key: str
    value: int
    label: Optional[str] = None
    unit: Optional[str] = None


class ThresholdOut(BaseModel):
    model_config = ORM
    id: str
    key: str
    label: Optional[str] = None
    value: int
    unit: Optional[str] = None


# -------------------------------------------------------------------- audit
class AuditOut(BaseModel):
    model_config = ORM
    id: int
    user_id: Optional[str] = None
    user_email: Optional[str] = None
    user_role: Optional[str] = None
    action: str
    entity_type: str
    entity_id: Optional[str] = None
    old_value: Optional[Any] = None
    new_value: Optional[Any] = None
    reason: Optional[str] = None
    ip_address: Optional[str] = None
    prev_hash: str
    row_hash: str
    timestamp: datetime


class TamperSimRequest(BaseModel):
    audit_id: int
    new_action: str = "SILENTLY_ALTERED"


class Paged(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[Any]
