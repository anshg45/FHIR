"""Background jobs (APScheduler): overdue detection, enrollment lag, Merkle commit."""
import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select, update

from .anchoring import commit_batch
from .config import settings
from .database import SessionLocal
from .models import (
    AdverseEvent,
    Alert,
    AlertThreshold,
    Milestone,
    ResearchStudy,
    VisitLog,
)

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLDS = [
    ("visit_overdue_days", "Days after the visit window before a visit is overdue", 3, "days"),
    ("enrollment_lag_percent", "Enrollment shortfall percentage that raises an alert", 25, "%"),
    ("ae_deadline_warning_hours", "Hours before a regulatory deadline to warn", 24, "hours"),
    ("query_ageing_days", "Days an open data query may remain unanswered", 7, "days"),
    ("iec_renewal_warning_days", "Days before IEC expiry to warn", 60, "days"),
]


def get_threshold(db, key: str, fallback: int) -> int:
    row = db.execute(select(AlertThreshold).where(AlertThreshold.key == key)).scalars().first()
    return row.value if row else fallback


def ensure_default_thresholds() -> None:
    db = SessionLocal()
    try:
        for key, label, value, unit in DEFAULT_THRESHOLDS:
            exists = (
                db.execute(select(AlertThreshold).where(AlertThreshold.key == key))
                .scalars()
                .first()
            )
            if not exists:
                db.add(AlertThreshold(key=key, label=label, value=value, unit=unit))
        db.commit()
    finally:
        db.close()


def _raise_alert(db, *, study_id, alert_type, severity, message, entity_type=None,
                 entity_id=None, target_roles=None):
    dup = (
        db.execute(
            select(Alert).where(
                Alert.alert_type == alert_type,
                Alert.entity_id == (str(entity_id) if entity_id else None),
                Alert.resolved.is_(False),
            )
        )
        .scalars()
        .first()
    )
    if dup:
        return None
    alert = Alert(
        study_id=study_id,
        alert_type=alert_type,
        severity=severity,
        message=message,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id else None,
        target_roles=target_roles or [],
    )
    db.add(alert)
    return alert


def refresh_overdue_visits() -> dict:
    """Mark scheduled visits past their window as overdue and alert."""
    db = SessionLocal()
    created = 0
    try:
        grace = get_threshold(db, "visit_overdue_days", 3)
        cutoff = date.today() - timedelta(days=grace)
        overdue = (
            db.execute(
                select(VisitLog).where(
                    VisitLog.status == "scheduled",
                    VisitLog.scheduled_date.isnot(None),
                    VisitLog.scheduled_date < cutoff,
                )
            )
            .scalars()
            .all()
        )
        for v in overdue:
            v.status = "overdue"
            v.deviation_flag = True
            if _raise_alert(
                db,
                study_id=v.study_id,
                alert_type="visit_overdue",
                severity="warning",
                message=(
                    f"Visit {v.visit_number} ({v.visit_name or 'unnamed'}) was scheduled for "
                    f"{v.scheduled_date} and is now overdue."
                ),
                entity_type="VisitLog",
                entity_id=v.id,
                target_roles=["coordinator", "monitor", "pi"],
            ):
                created += 1
        db.commit()
        return {"visits_marked_overdue": len(overdue), "alerts_created": created}
    finally:
        db.close()


def refresh_overdue_milestones() -> dict:
    db = SessionLocal()
    try:
        result = db.execute(
            update(Milestone)
            .where(
                Milestone.status == "pending",
                Milestone.due_date.isnot(None),
                Milestone.due_date < date.today(),
            )
            .values(status="overdue")
        )
        overdue = (
            db.execute(select(Milestone).where(Milestone.status == "overdue")).scalars().all()
        )
        for m in overdue:
            _raise_alert(
                db,
                study_id=m.study_id,
                alert_type="milestone_overdue",
                severity="warning",
                message=f"Milestone '{m.milestone_type}' was due on {m.due_date}.",
                entity_type="Milestone",
                entity_id=m.id,
                target_roles=["pi", "admin", m.owner_role or "admin"],
            )
        db.commit()
        return {"milestones_marked_overdue": result.rowcount or 0}
    finally:
        db.close()


def check_enrollment_lag() -> dict:
    db = SessionLocal()
    flagged = []
    try:
        lag_pct = get_threshold(db, "enrollment_lag_percent", 25)
        studies = (
            db.execute(select(ResearchStudy).where(ResearchStudy.status == "active"))
            .scalars()
            .all()
        )
        today = date.today()
        for s in studies:
            if not s.enrollment_target or not s.start_date or not s.end_date:
                continue
            total_days = max((s.end_date - s.start_date).days, 1)
            elapsed = max((today - s.start_date).days, 0)
            expected = min(s.enrollment_target * elapsed / total_days, s.enrollment_target)
            if expected <= 0:
                continue
            shortfall = (expected - s.enrolled_count) / expected * 100
            if shortfall >= lag_pct:
                flagged.append(
                    {
                        "study_id": s.id,
                        "protocol_number": s.protocol_number,
                        "expected": round(expected, 1),
                        "actual": s.enrolled_count,
                        "shortfall_percent": round(shortfall, 1),
                    }
                )
                _raise_alert(
                    db,
                    study_id=s.id,
                    alert_type="enrollment_lag",
                    severity="warning",
                    message=(
                        f"Enrollment is {round(shortfall, 1)}% behind plan "
                        f"({s.enrolled_count} enrolled vs {round(expected, 1)} expected)."
                    ),
                    entity_type="ResearchStudy",
                    entity_id=s.id,
                    target_roles=["pi", "admin", "monitor"],
                )
        db.commit()
        return {"studies_flagged": flagged}
    finally:
        db.close()


def check_ae_deadlines() -> dict:
    db = SessionLocal()
    try:
        warn_h = get_threshold(db, "ae_deadline_warning_hours", 24)
        now = datetime.now(timezone.utc)
        horizon = now + timedelta(hours=warn_h)
        aes = (
            db.execute(
                select(AdverseEvent).where(
                    AdverseEvent.regulatory_deadline.isnot(None),
                    AdverseEvent.reported_to_authority_at.is_(None),
                    AdverseEvent.regulatory_deadline <= horizon,
                )
            )
            .scalars()
            .all()
        )
        breached = 0
        for ae in aes:
            dl = ae.regulatory_deadline
            if dl.tzinfo is None:
                dl = dl.replace(tzinfo=timezone.utc)
            is_breached = dl < now
            breached += 1 if is_breached else 0
            _raise_alert(
                db,
                study_id=ae.study_id,
                alert_type="ae_deadline_breached" if is_breached else "ae_deadline_due_soon",
                severity="critical" if is_breached else "warning",
                message=(
                    f"{'BREACHED' if is_breached else 'Due soon'}: regulatory reporting "
                    f"deadline for {ae.ae_number} is {dl.isoformat()}."
                ),
                entity_type="AdverseEvent",
                entity_id=ae.id,
                target_roles=["pv", "admin", "ec"],
            )
        db.commit()
        return {"ae_deadlines_flagged": len(aes), "breached": breached}
    finally:
        db.close()


def check_iec_renewals() -> dict:
    db = SessionLocal()
    try:
        warn_days = get_threshold(db, "iec_renewal_warning_days", 60)
        horizon = date.today() + timedelta(days=warn_days)
        studies = (
            db.execute(
                select(ResearchStudy).where(
                    ResearchStudy.iec_renewal_due.isnot(None),
                    ResearchStudy.iec_renewal_due <= horizon,
                    ResearchStudy.status.in_(["active", "ctri_registered", "ec_approved"]),
                )
            )
            .scalars()
            .all()
        )
        for s in studies:
            expired = s.iec_renewal_due < date.today()
            _raise_alert(
                db,
                study_id=s.id,
                alert_type="iec_renewal_expired" if expired else "iec_renewal_due",
                severity="critical" if expired else "warning",
                message=(
                    f"IEC approval for {s.protocol_number} "
                    f"{'EXPIRED on' if expired else 'expires on'} {s.iec_renewal_due}."
                ),
                entity_type="ResearchStudy",
                entity_id=s.id,
                target_roles=["pi", "ec", "admin"],
            )
        db.commit()
        return {"iec_renewals_flagged": len(studies)}
    finally:
        db.close()


def periodic_merkle_commit() -> dict:
    db = SessionLocal()
    try:
        return commit_batch(db, force=False)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Periodic Merkle commit failed")
        return {"anchored": False, "reason": str(exc)}
    finally:
        db.close()


def run_all_jobs() -> dict:
    return {
        "overdue_visits": refresh_overdue_visits(),
        "overdue_milestones": refresh_overdue_milestones(),
        "enrollment_lag": check_enrollment_lag(),
        "ae_deadlines": check_ae_deadlines(),
        "iec_renewals": check_iec_renewals(),
        "merkle_commit": periodic_merkle_commit(),
    }


_scheduler = None


def start_scheduler():
    global _scheduler
    if not settings.ENABLE_SCHEDULER or _scheduler is not None:
        return _scheduler
    from apscheduler.schedulers.background import BackgroundScheduler

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(refresh_overdue_visits, "interval", minutes=30, id="overdue_visits")
    _scheduler.add_job(refresh_overdue_milestones, "interval", minutes=30, id="overdue_milestones")
    _scheduler.add_job(check_enrollment_lag, "interval", hours=6, id="enrollment_lag")
    _scheduler.add_job(check_ae_deadlines, "interval", minutes=15, id="ae_deadlines")
    _scheduler.add_job(check_iec_renewals, "interval", hours=12, id="iec_renewals")
    _scheduler.add_job(periodic_merkle_commit, "interval", minutes=10, id="merkle_commit")
    _scheduler.start()
    logger.info("Background scheduler started with %d jobs", len(_scheduler.get_jobs()))
    return _scheduler


def shutdown_scheduler():
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
