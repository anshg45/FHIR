"""Domain enumerations (kept as plain str constants for JSON friendliness)."""
from enum import Enum


class Role(str, Enum):
    PI = "pi"
    COORDINATOR = "coordinator"
    MONITOR = "monitor"
    EC = "ec"
    PV = "pv"
    ADMIN = "admin"
    REGULATOR = "regulator"


ALL_ROLES = [r.value for r in Role]
READ_ONLY_ROLES = [Role.REGULATOR.value]


class StudyStatus(str, Enum):
    DRAFT = "draft"
    PROTOCOL_SUBMITTED = "protocol_submitted"
    EC_APPROVED = "ec_approved"
    EC_REJECTED = "ec_rejected"
    CTRI_REGISTERED = "ctri_registered"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    COMPLETED = "completed"
    TERMINATED = "terminated"


class CtriStatus(str, Enum):
    NOT_SUBMITTED = "not_submitted"
    SUBMITTED = "submitted"
    REGISTERED = "registered"
    REJECTED = "rejected"


class IecStatus(str, Enum):
    NOT_SUBMITTED = "not_submitted"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class PatientStatus(str, Enum):
    SCREENED = "screened"
    ENROLLED = "enrolled"
    COMPLETED = "completed"
    WITHDRAWN = "withdrawn"
    SCREEN_FAILED = "screen_failed"


class Seriousness(str, Enum):
    SERIOUS = "serious"
    NON_SERIOUS = "non_serious"


class AeSeverity(str, Enum):
    MILD = "mild"
    MODERATE = "moderate"
    SEVERE = "severe"


class AeOutcome(str, Enum):
    RECOVERED = "recovered"
    RECOVERING = "recovering"
    ONGOING = "ongoing"
    RECOVERED_WITH_SEQUELAE = "recovered_with_sequelae"
    FATAL = "fatal"
    UNKNOWN = "unknown"


class AeStatus(str, Enum):
    OPEN = "open"
    UNDER_REVIEW = "under_review"
    CODED = "coded"
    RESOLVED = "resolved"
    REPORTED = "reported"


class Causality(str, Enum):
    CERTAIN = "certain"
    PROBABLE = "probable"
    POSSIBLE = "possible"
    UNLIKELY = "unlikely"
    UNASSESSABLE = "unassessable"
    NOT_RELATED = "not_related"


class VisitStatus(str, Enum):
    SCHEDULED = "scheduled"
    COMPLETED = "completed"
    MISSED = "missed"
    OVERDUE = "overdue"


class DeviationSeverity(str, Enum):
    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"


class QueryStatus(str, Enum):
    OPEN = "open"
    ANSWERED = "answered"
    CLOSED = "closed"


class MilestoneStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    OVERDUE = "overdue"


class VerificationStatus(str, Enum):
    PENDING = "pending"
    MATCHED = "matched"
    MISMATCHED = "mismatched"
