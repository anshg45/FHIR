"""PostgreSQL data model for the AIIA CTMS. FHIR R4 aligned."""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Index,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


# ---------------------------------------------------------------- Sites/Users
class Site(Base, TimestampMixin):
    __tablename__ = "sites"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    city: Mapped[str | None] = mapped_column(String(100))
    state: Mapped[str | None] = mapped_column(String(100))
    country: Mapped[str] = mapped_column(String(100), default="India")
    contact_person: Mapped[str | None] = mapped_column(String(255))
    contact_email: Mapped[str | None] = mapped_column(String(255))
    contact_phone: Mapped[str | None] = mapped_column(String(50))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    designation: Mapped[str | None] = mapped_column(String(150))
    phone: Mapped[str | None] = mapped_column(String(50))
    site_id: Mapped[str | None] = mapped_column(ForeignKey("sites.id", ondelete="SET NULL"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    site = relationship("Site", lazy="joined")


class StudyAssignment(Base, TimestampMixin):
    """Coordinator / monitor assignment to a study."""

    __tablename__ = "study_assignments"
    __table_args__ = (UniqueConstraint("study_id", "user_id", name="uq_study_user"),)

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    study_id: Mapped[str] = mapped_column(ForeignKey("research_studies.id", ondelete="CASCADE"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role_on_study: Mapped[str | None] = mapped_column(String(50))


# ------------------------------------------------------------ ResearchStudy
class ResearchStudy(Base, TimestampMixin):
    __tablename__ = "research_studies"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    protocol_number: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="draft", index=True)
    phase: Mapped[str | None] = mapped_column(String(40))
    study_design: Mapped[str | None] = mapped_column(String(120))
    sponsor: Mapped[str | None] = mapped_column(String(255))
    therapeutic_area: Mapped[str | None] = mapped_column(String(150))
    condition: Mapped[str | None] = mapped_column(String(255))
    intervention: Mapped[str | None] = mapped_column(String(255))

    # CTRI (manual entry - no public write API exists)
    ctri_registration_number: Mapped[str | None] = mapped_column(String(80))
    ctri_status: Mapped[str] = mapped_column(String(30), default="not_submitted")
    ctri_registration_date: Mapped[date | None] = mapped_column(Date)

    # Institutional Ethics Committee
    iec_approval_status: Mapped[str] = mapped_column(String(30), default="not_submitted")
    iec_approval_number: Mapped[str | None] = mapped_column(String(80))
    iec_approval_date: Mapped[date | None] = mapped_column(Date)
    iec_renewal_due: Mapped[date | None] = mapped_column(Date)
    iec_remarks: Mapped[str | None] = mapped_column(Text)

    enrollment_target: Mapped[int] = mapped_column(Integer, default=0)
    enrolled_count: Mapped[int] = mapped_column(Integer, default=0)

    site_id: Mapped[str | None] = mapped_column(ForeignKey("sites.id", ondelete="SET NULL"))
    principal_investigator_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    data_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    closeout_signed_by: Mapped[str | None] = mapped_column(String(255))
    closeout_signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    site = relationship("Site", lazy="joined")
    principal_investigator = relationship("User", lazy="joined")


# ------------------------------------------------------------------ Patient
class Patient(Base, TimestampMixin):
    __tablename__ = "patients"
    __table_args__ = (
        UniqueConstraint("study_id", "screening_number", name="uq_study_screening"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    study_id: Mapped[str] = mapped_column(
        ForeignKey("research_studies.id", ondelete="CASCADE"), index=True
    )
    screening_number: Mapped[str] = mapped_column(String(60), nullable=False)
    randomization_number: Mapped[str | None] = mapped_column(String(60))
    subject_initials: Mapped[str | None] = mapped_column(String(10))
    screening_date: Mapped[date | None] = mapped_column(Date)
    enrollment_date: Mapped[date | None] = mapped_column(Date)
    completion_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(30), default="screened", index=True)
    age: Mapped[int | None] = mapped_column(Integer)
    sex: Mapped[str | None] = mapped_column(String(20))  # male|female|other|unknown
    arm: Mapped[str | None] = mapped_column(String(120))

    # Informed consent (ICH-GCP)
    consent_obtained: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_date: Mapped[date | None] = mapped_column(Date)
    consent_version: Mapped[str | None] = mapped_column(String(40))
    consent_language: Mapped[str | None] = mapped_column(String(60))

    withdrawal_reason: Mapped[str | None] = mapped_column(Text)
    screen_failure_reason: Mapped[str | None] = mapped_column(Text)
    site_id: Mapped[str | None] = mapped_column(ForeignKey("sites.id", ondelete="SET NULL"))

    study = relationship("ResearchStudy", lazy="joined")


# ------------------------------------------------------------- AdverseEvent
class AdverseEvent(Base, TimestampMixin):
    __tablename__ = "adverse_events"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    ae_number: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    study_id: Mapped[str] = mapped_column(
        ForeignKey("research_studies.id", ondelete="CASCADE"), index=True
    )
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.id", ondelete="CASCADE"), index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)

    # Synthetic MedDRA stub (real MedDRA requires a paid licence)
    ae_term: Mapped[str | None] = mapped_column(String(255))
    meddra_code: Mapped[str | None] = mapped_column(String(40))
    meddra_pt: Mapped[str | None] = mapped_column(String(255))
    meddra_soc: Mapped[str | None] = mapped_column(String(255))
    coding_dictionary: Mapped[str | None] = mapped_column(String(80))
    coded_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    coded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    seriousness: Mapped[str] = mapped_column(String(20), default="non_serious", index=True)
    seriousness_criteria: Mapped[str | None] = mapped_column(String(255))
    severity: Mapped[str | None] = mapped_column(String(20))
    causality: Mapped[str | None] = mapped_column(String(30))
    outcome: Mapped[str | None] = mapped_column(String(40))
    action_taken: Mapped[str | None] = mapped_column(String(150))
    suspect_intervention: Mapped[str | None] = mapped_column(String(255))

    onset_date: Mapped[date | None] = mapped_column(Date)
    resolution_date: Mapped[date | None] = mapped_column(Date)
    report_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    regulatory_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    followup_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reported_to_authority_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    reported_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(30), default="open", index=True)
    sae_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    sae_confirmed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    sae_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ec_escalated: Mapped[bool] = mapped_column(Boolean, default=False)
    ec_escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ec_acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    dsmb_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    npvcc_reference: Mapped[str | None] = mapped_column(String(80))
    narrative: Mapped[str | None] = mapped_column(Text)

    study = relationship("ResearchStudy", lazy="joined")
    patient = relationship("Patient", lazy="joined")


# ----------------------------------------------------------------- VisitLog
class VisitLog(Base, TimestampMixin):
    __tablename__ = "visit_logs"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    study_id: Mapped[str] = mapped_column(
        ForeignKey("research_studies.id", ondelete="CASCADE"), index=True
    )
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.id", ondelete="CASCADE"), index=True)
    visit_number: Mapped[int] = mapped_column(Integer, nullable=False)
    visit_name: Mapped[str | None] = mapped_column(String(150))
    scheduled_date: Mapped[date | None] = mapped_column(Date)
    actual_date: Mapped[date | None] = mapped_column(Date)
    window_days: Mapped[int] = mapped_column(Integer, default=3)
    status: Mapped[str] = mapped_column(String(30), default="scheduled", index=True)
    deviation_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    performed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    notes: Mapped[str | None] = mapped_column(Text)

    patient = relationship("Patient", lazy="joined")


class ProtocolDeviation(Base, TimestampMixin):
    __tablename__ = "protocol_deviations"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    study_id: Mapped[str] = mapped_column(
        ForeignKey("research_studies.id", ondelete="CASCADE"), index=True
    )
    patient_id: Mapped[str | None] = mapped_column(ForeignKey("patients.id", ondelete="CASCADE"))
    visit_log_id: Mapped[str | None] = mapped_column(ForeignKey("visit_logs.id", ondelete="SET NULL"))
    deviation_number: Mapped[str | None] = mapped_column(String(60))
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(String(120))
    severity: Mapped[str] = mapped_column(String(20), default="minor", index=True)
    reported_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reported_date: Mapped[date | None] = mapped_column(Date)
    corrective_action: Mapped[str | None] = mapped_column(Text)
    resolution: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="open")
    reviewed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DataQuery(Base, TimestampMixin):
    __tablename__ = "data_queries"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    study_id: Mapped[str] = mapped_column(
        ForeignKey("research_studies.id", ondelete="CASCADE"), index=True
    )
    patient_id: Mapped[str | None] = mapped_column(ForeignKey("patients.id", ondelete="CASCADE"))
    entity_type: Mapped[str | None] = mapped_column(String(60))
    entity_id: Mapped[str | None] = mapped_column(String(80))
    field_name: Mapped[str | None] = mapped_column(String(120))
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(String(20), default="medium")
    raised_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    raised_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=_now)
    response_text: Mapped[str | None] = mapped_column(Text)
    responded_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    responded_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    closed_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(30), default="open", index=True)


class Milestone(Base, TimestampMixin):
    __tablename__ = "milestones"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    study_id: Mapped[str] = mapped_column(
        ForeignKey("research_studies.id", ondelete="CASCADE"), index=True
    )
    milestone_type: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    due_date: Mapped[date | None] = mapped_column(Date, index=True)
    completed_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    owner_role: Mapped[str | None] = mapped_column(String(40))


class MonitoringVisitReport(Base, TimestampMixin):
    __tablename__ = "monitoring_visit_reports"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    study_id: Mapped[str] = mapped_column(
        ForeignKey("research_studies.id", ondelete="CASCADE"), index=True
    )
    monitor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    visit_date: Mapped[date | None] = mapped_column(Date)
    visit_type: Mapped[str | None] = mapped_column(String(60))
    findings: Mapped[str | None] = mapped_column(Text)
    action_items: Mapped[str | None] = mapped_column(Text)
    subjects_reviewed: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(30), default="draft")


class AlertThreshold(Base, TimestampMixin):
    __tablename__ = "alert_thresholds"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    key: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    label: Mapped[str | None] = mapped_column(String(255))
    value: Mapped[int] = mapped_column(Integer, default=0)
    unit: Mapped[str | None] = mapped_column(String(40))
    updated_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class Alert(Base, TimestampMixin):
    __tablename__ = "alerts"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    study_id: Mapped[str | None] = mapped_column(ForeignKey("research_studies.id", ondelete="CASCADE"))
    alert_type: Mapped[str] = mapped_column(String(80), index=True)
    severity: Mapped[str] = mapped_column(String(20), default="info")
    message: Mapped[str] = mapped_column(Text)
    entity_type: Mapped[str | None] = mapped_column(String(60))
    entity_id: Mapped[str | None] = mapped_column(String(80))
    target_roles: Mapped[list | None] = mapped_column(JSONB)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)


# ------------------------------------------------- Immutable audit structures
class AuditTrail(Base):
    """Append-only audit log. UPDATE/DELETE blocked by PostgreSQL triggers."""

    __tablename__ = "audit_trail"
    __table_args__ = (Index("ix_audit_entity", "entity_type", "entity_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    user_email: Mapped[str | None] = mapped_column(String(255))
    user_role: Mapped[str | None] = mapped_column(String(40))
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(120))
    old_value: Mapped[dict | None] = mapped_column(JSONB)
    new_value: Mapped[dict | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    row_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False, index=True
    )


class AuditAnchor(Base):
    """Merkle-root anchor batches. Append-only."""

    __tablename__ = "audit_anchors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_start_id: Mapped[int] = mapped_column(Integer, nullable=False)
    batch_end_id: Mapped[int] = mapped_column(Integer, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    merkle_root: Mapped[str] = mapped_column(String(66), nullable=False)
    chain: Mapped[str] = mapped_column(String(60), default="local")
    tx_hash: Mapped[str | None] = mapped_column(String(120))
    block_number: Mapped[int | None] = mapped_column(Integer)
    explorer_url: Mapped[str | None] = mapped_column(String(255))
    committed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    verification_status: Mapped[str] = mapped_column(String(20), default="pending")
    notes: Mapped[str | None] = mapped_column(Text)
