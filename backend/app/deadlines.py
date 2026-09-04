"""Regulatory reporting deadline calculation (NDCT Rules 2019 style).

SAE that is fatal / life-threatening : initial report within 24 hours,
                                       detailed report within 14 days
Other serious adverse event          : within 15 calendar days
Non-serious adverse event            : within 30 calendar days

These windows are configurable in app.config.
"""
from datetime import datetime, timedelta, timezone

from .config import settings

FATAL_OUTCOMES = {"fatal", "life_threatening"}
FATAL_CRITERIA_KEYWORDS = ("death", "life-threatening", "life threatening")


def compute_deadlines(
    *,
    seriousness: str,
    outcome: str | None = None,
    seriousness_criteria: str | None = None,
    report_date: datetime | None = None,
) -> dict:
    base = report_date or datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)

    is_fatal = (outcome or "").lower() in FATAL_OUTCOMES or any(
        k in (seriousness_criteria or "").lower() for k in FATAL_CRITERIA_KEYWORDS
    )

    if seriousness == "serious" and is_fatal:
        return {
            "regulatory_deadline": base + timedelta(hours=settings.SAE_FATAL_WINDOW_HOURS),
            "followup_deadline": base + timedelta(days=settings.SAE_FATAL_FOLLOWUP_DAYS),
            "rule": "SAE (death / life-threatening): 24-hour initial report, 14-day detailed report",
            "priority": "critical",
        }
    if seriousness == "serious":
        return {
            "regulatory_deadline": base + timedelta(days=settings.SAE_OTHER_WINDOW_DAYS),
            "followup_deadline": None,
            "rule": "Serious adverse event: 15 calendar days",
            "priority": "high",
        }
    return {
        "regulatory_deadline": base + timedelta(days=settings.NON_SERIOUS_WINDOW_DAYS),
        "followup_deadline": None,
        "rule": "Non-serious adverse event: 30 calendar days",
        "priority": "normal",
    }


def deadline_state(deadline: datetime | None, now: datetime | None = None) -> dict:
    if deadline is None:
        return {"state": "not_applicable", "hours_remaining": None}
    now = now or datetime.now(timezone.utc)
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    delta = deadline - now
    hours = round(delta.total_seconds() / 3600, 2)
    if hours < 0:
        state = "breached"
    elif hours <= 24:
        state = "due_soon"
    else:
        state = "on_track"
    return {"state": state, "hours_remaining": hours, "deadline": deadline.isoformat()}
