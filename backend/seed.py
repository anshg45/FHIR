"""Synthetic Ayurveda clinical-trial seed data.

Populates the database with a realistic demo dataset: 7 users covering the 7
roles, 2 sites, 3 studies at different life-cycle stages, patients (screened /
enrolled / withdrawn / completed), a full visit schedule per subject, a mix of
serious + non-serious adverse events, deviations and data queries.

Run:  cd /app/backend && python seed.py
"""
from __future__ import annotations

import logging
import os
import random
from datetime import date, datetime, timedelta, timezone

from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import log_action
from app.database import SessionLocal, init_db
from app.deadlines import compute_deadlines
from app.jobs import ensure_default_thresholds
from app.meddra import autocode
from app.models import (
    AdverseEvent,
    Milestone,
    Patient,
    ProtocolDeviation,
    ResearchStudy,
    Site,
    StudyAssignment,
    User,
    VisitLog,
)
from app.security import hash_password

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("seed")
random.seed(42)

# Demo password for the 7 seeded role accounts. Sourced from the environment so
# secrets never live in source control; falls back to a public demo default so
# a first-time reviewer can still boot the system without extra setup.
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
DEMO_PASSWORD: str = os.environ.get("SEED_DEMO_PASSWORD", "Aiia@2025")


USERS = [
    ("Dr. Anjali Sharma", "pi@aiia.gov.in", "pi", "Professor of Kayachikitsa"),
    ("Rahul Verma", "coordinator@aiia.gov.in", "coordinator", "Study Coordinator"),
    ("Dr. Priya Nair", "monitor@aiia.gov.in", "monitor", "Clinical Monitor"),
    ("Dr. Suresh Iyer", "ec@aiia.gov.in", "ec", "Ethics Committee Member Secretary"),
    ("Dr. Meera Joshi", "pv@aiia.gov.in", "pv", "NPvCC Pharmacovigilance Officer"),
    ("Amit Kulkarni", "admin@aiia.gov.in", "admin", "CTMS Administrator"),
    ("Dr. R. K. Menon", "regulator@aiia.gov.in", "regulator", "CDSCO / CCRAS Inspector"),
]

SITES = [
    ("AIIA Delhi Main Campus", "AIIA-DEL", "New Delhi", "Delhi"),
    ("AIIA Panchakarma Wing", "AIIA-PK", "New Delhi", "Delhi"),
]


AYUR_STUDIES = [
    {
        "protocol_number": "AIIA-KV-2025-01",
        "title": (
            "Randomised, Double-Blind Trial of Kutkyadi Vati vs Placebo in "
            "Yakrit Vikara (Non-Alcoholic Fatty Liver Disease)"
        ),
        "description": (
            "Ayurvedic polyherbal formulation Kutkyadi Vati (Kutki, Bhumiamalaki, "
            "Punarnava) evaluated against matched placebo for hepato-protective "
            "efficacy in NAFLD as per Ayurveda Yakrit Vikara diagnostic criteria."
        ),
        "phase": "Phase 2",
        "study_design": "Randomised, double-blind, placebo-controlled",
        "sponsor": "All India Institute of Ayurveda, Ministry of AYUSH",
        "therapeutic_area": "Kayachikitsa - Hepatology",
        "condition": "Non-Alcoholic Fatty Liver Disease (Yakrit Vikara)",
        "intervention": "Kutkyadi Vati 500 mg BID x 12 weeks",
        "enrollment_target": 60,
        "target_lifecycle": "active",
    },
    {
        "protocol_number": "AIIA-AS-2025-02",
        "title": (
            "Efficacy of Ashwagandha Ghrita in Chittodvega (Generalised Anxiety "
            "Disorder): An Open-Label Pilot Study"
        ),
        "description": (
            "Open-label pilot evaluating standardised Ashwagandha Ghrita in "
            "adults with mild-to-moderate GAD (Chittodvega), measured by "
            "Hamilton Anxiety Rating Scale and Ayurveda Manas Prakriti."
        ),
        "phase": "Phase 2",
        "study_design": "Open-label, single-arm",
        "sponsor": "CCRAS - Central Council for Research in Ayurvedic Sciences",
        "therapeutic_area": "Manas Roga (Psychiatry)",
        "condition": "Generalised Anxiety Disorder (Chittodvega)",
        "intervention": "Ashwagandha Ghrita 10 g OD x 8 weeks",
        "enrollment_target": 30,
        "target_lifecycle": "active",
    },
    {
        "protocol_number": "AIIA-PK-2025-03",
        "title": (
            "Comparative Effectiveness of Classical Panchakarma vs Standard "
            "Physiotherapy in Sandhigata Vata (Osteoarthritis of Knee)"
        ),
        "description": (
            "Two-arm effectiveness trial comparing classical Panchakarma "
            "(Abhyanga, Swedana, Basti) against WHO standard physiotherapy in "
            "grade 2-3 knee OA."
        ),
        "phase": "Phase 3",
        "study_design": "Two-arm, parallel group, blinded outcome assessor",
        "sponsor": "Ministry of AYUSH",
        "therapeutic_area": "Panchakarma - Rheumatology",
        "condition": "Osteoarthritis of Knee (Sandhigata Vata)",
        "intervention": "Classical Panchakarma course x 21 days",
        "enrollment_target": 90,
        "target_lifecycle": "ec_approved",
    },
]


AE_SAMPLES = [
    ("Loose motions after third dose", "non_serious", "mild", "possible", "recovered"),
    ("Mild nausea for 2 days", "non_serious", "mild", "possible", "recovered"),
    ("Headache and dizziness", "non_serious", "moderate", "possible", "recovering"),
    ("Skin rash on forearms", "non_serious", "mild", "possible", "recovered"),
    ("Elevated SGPT on week-8 lab", "serious", "moderate", "probable", "recovering"),
    ("Anaphylactic reaction requiring ER visit", "serious", "severe", "probable", "recovered"),
    ("Persistent joint pain flare", "non_serious", "moderate", "unlikely", "ongoing"),
    ("Sleeplessness for 5 nights", "non_serious", "mild", "possible", "recovered"),
]

DEVIATIONS = [
    ("Visit performed 5 days outside window", "Visit window deviation", "minor"),
    ("Consent re-signed with amended version delayed", "Consent process", "major"),
    ("Study drug administered before dispensing log entry", "Drug accountability", "major"),
]


def _upsert_site(db: Session, name: str, code: str, city: str, state: str) -> Site:
    row = db.execute(select(Site).where(Site.code == code)).scalars().first()
    if row:
        return row
    row = Site(name=name, code=code, city=city, state=state, country="India",
               contact_person="Site Coordinator", contact_email=f"{code.lower()}@aiia.gov.in",
               contact_phone="+91-11-40000000", is_active=True)
    db.add(row)
    db.flush()
    return row


def _upsert_user(db: Session, name: str, email: str, role: str, designation: str,
                 site_id: str | None = None) -> User:
    row = db.execute(select(User).where(User.email == email)).scalars().first()
    if row:
        return row
    row = User(name=name, email=email, role=role, designation=designation,
               phone="+91-9876543210", site_id=site_id, is_active=True,
               password_hash=hash_password(DEMO_PASSWORD))
    db.add(row)
    db.flush()
    return row


def _visit_dates(start: date, count: int, interval: int) -> list[date]:
    return [start + timedelta(days=interval * i) for i in range(count)]


def _create_study(db: Session, spec: dict, site: Site, pi: User, coordinator: User,
                  monitor: User, admin: User) -> ResearchStudy:
    existing = db.execute(
        select(ResearchStudy).where(ResearchStudy.protocol_number == spec["protocol_number"])
    ).scalars().first()
    if existing:
        return existing
    lifecycle = spec.pop("target_lifecycle", "active")
    today = date.today()
    start_date = today - timedelta(days=180)
    end_date = today + timedelta(days=180)

    study = ResearchStudy(
        protocol_number=spec["protocol_number"],
        title=spec["title"],
        description=spec["description"],
        phase=spec["phase"],
        study_design=spec["study_design"],
        sponsor=spec["sponsor"],
        therapeutic_area=spec["therapeutic_area"],
        condition=spec["condition"],
        intervention=spec["intervention"],
        enrollment_target=spec["enrollment_target"],
        site_id=site.id,
        principal_investigator_id=pi.id,
        start_date=start_date,
        end_date=end_date,
        status="draft",
    )
    if lifecycle in ("ec_approved", "ctri_registered", "active"):
        study.iec_approval_status = "approved"
        study.iec_approval_number = f"IEC/AIIA/{spec['protocol_number']}"
        study.iec_approval_date = start_date - timedelta(days=30)
        study.iec_renewal_due = study.iec_approval_date + timedelta(days=365)
        study.status = "ec_approved"
    if lifecycle in ("ctri_registered", "active"):
        study.ctri_status = "registered"
        study.ctri_registration_number = f"CTRI/2025/01/{random.randint(10000, 99999)}"
        study.ctri_registration_date = start_date - timedelta(days=15)
        study.status = "ctri_registered"
    if lifecycle == "active":
        study.status = "active"
    db.add(study)
    db.flush()

    # milestones
    plan = [
        ("Protocol finalisation", 0, "pi", "completed"),
        ("IEC submission", 14, "pi", "completed"),
        ("IEC approval", 45, "ec",
         "completed" if study.iec_approval_status == "approved" else "pending"),
        ("CTRI registration", 60, "pi",
         "completed" if study.ctri_status == "registered" else "pending"),
        ("Site activation", 75, "admin",
         "completed" if study.status == "active" else "pending"),
        ("First subject enrolled", 90, "coordinator",
         "completed" if study.status == "active" else "pending"),
        ("50% enrollment", 180, "coordinator", "pending"),
        ("Last subject enrolled", 300, "coordinator", "pending"),
        ("Database lock", 360, "admin", "pending"),
        ("SDTM export / regulatory submission", 390, "admin", "pending"),
    ]
    for name, offset, owner, status in plan:
        db.add(
            Milestone(
                study_id=study.id,
                milestone_type=name,
                due_date=start_date + timedelta(days=offset),
                completed_date=(start_date + timedelta(days=offset))
                if status == "completed" else None,
                status=status,
                owner_role=owner,
            )
        )

    # assignments
    for user in (coordinator, monitor):
        db.add(StudyAssignment(study_id=study.id, user_id=user.id,
                               role_on_study=user.role))
    return study


def _create_patients(db: Session, study: ResearchStudy, pi: User,
                     coordinator: User, count: int) -> list[Patient]:
    if study.status != "active":
        return []
    existing = db.execute(select(Patient).where(Patient.study_id == study.id)).scalars().all()
    if existing:
        return existing
    subjects = []
    for i in range(count):
        sn = f"{study.protocol_number}-S{i + 1:04d}"
        sex = random.choice(["male", "female"])
        age = random.randint(28, 65)
        screening_date = (study.start_date or date.today()) + timedelta(days=random.randint(0, 90))
        p = Patient(
            study_id=study.id,
            screening_number=sn,
            subject_initials="".join(random.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ", k=3)),
            screening_date=screening_date,
            age=age,
            sex=sex,
            site_id=study.site_id,
            status="screened",
        )
        db.add(p)
        db.flush()
        subjects.append(p)

    # Enroll ~70%, screen-fail ~15%, withdraw ~10%, complete ~5%
    enrolled = []
    for i, p in enumerate(subjects):
        r = i % 10
        if r == 0:
            p.status = "screen_failed"
            p.screen_failure_reason = "Excluded per Yakrit Vikara diagnostic criteria"
        elif r == 1:
            p.status = "withdrawn"
            p.enrollment_date = p.screening_date + timedelta(days=3)
            p.randomization_number = f"{study.protocol_number}-R{i:04d}"
            p.arm = random.choice(["Test", "Control"])
            p.consent_obtained = True
            p.consent_date = p.screening_date
            p.consent_version = "v2.1"
            p.consent_language = "Hindi"
            p.completion_date = p.enrollment_date + timedelta(days=random.randint(14, 60))
            p.withdrawal_reason = "Withdrew consent due to work relocation"
            enrolled.append(p)
        else:
            p.status = "enrolled"
            p.enrollment_date = p.screening_date + timedelta(days=3)
            p.randomization_number = f"{study.protocol_number}-R{i:04d}"
            p.arm = random.choice(["Kutkyadi Vati", "Placebo"]) \
                if "KV" in study.protocol_number else random.choice(["Test", "Control"])
            p.consent_obtained = True
            p.consent_date = p.screening_date
            p.consent_version = "v2.1"
            p.consent_language = random.choice(["Hindi", "English", "Sanskrit-primer"])
            enrolled.append(p)
            if r == 2:
                p.status = "completed"
                p.completion_date = p.enrollment_date + timedelta(days=84)
    study.enrolled_count = sum(1 for p in subjects if p.status in ("enrolled", "completed", "withdrawn"))
    db.flush()
    return enrolled


def _create_visits(db: Session, study: ResearchStudy, patients: list[Patient]) -> None:
    names = ["Baseline", "Week 2", "Week 4", "Week 8", "Week 12"]
    for p in patients:
        if p.enrollment_date is None:
            continue
        base = p.enrollment_date
        for i, name in enumerate(names):
            sched = base + timedelta(days=14 * i)
            if sched > date.today() + timedelta(days=7):
                status = "scheduled"
                actual = None
            elif random.random() < 0.08:
                status = "missed"
                actual = None
            else:
                status = "completed"
                drift = random.randint(-2, 2)
                actual = sched + timedelta(days=drift)
            db.add(
                VisitLog(
                    study_id=study.id,
                    patient_id=p.id,
                    visit_number=i + 1,
                    visit_name=name,
                    scheduled_date=sched,
                    actual_date=actual,
                    window_days=3,
                    status=status,
                    deviation_flag=(status == "missed"),
                    notes=None,
                )
            )


def _create_aes(db: Session, study: ResearchStudy, patients: list[Patient],
                pv_user: User) -> None:
    seq = 0
    for i, p in enumerate(patients):
        if p.enrollment_date is None:
            continue
        n_events = 2 if i % 4 == 0 else (1 if i % 2 == 0 else 0)
        for _ in range(n_events):
            desc, serious, sev, causality, outcome = random.choice(AE_SAMPLES)
            seq += 1
            onset = p.enrollment_date + timedelta(days=random.randint(3, 60))
            report_time = datetime.combine(onset + timedelta(days=1),
                                           datetime.min.time(), tzinfo=timezone.utc)
            deadlines = compute_deadlines(
                seriousness=serious, outcome=outcome,
                seriousness_criteria=None, report_date=report_time,
            )
            hit = autocode(desc)
            ae = AdverseEvent(
                study_id=study.id,
                patient_id=p.id,
                ae_number=f"{study.protocol_number}-AE{seq:04d}",
                description=desc,
                ae_term=desc[:150],
                seriousness=serious,
                seriousness_criteria=(
                    "Hospitalisation required" if serious == "serious" else None
                ),
                severity=sev,
                causality=causality,
                outcome=outcome,
                onset_date=onset,
                resolution_date=onset + timedelta(days=random.randint(3, 20))
                if outcome in ("recovered", "recovering") else None,
                report_date=report_time,
                regulatory_deadline=deadlines["regulatory_deadline"],
                followup_deadline=deadlines["followup_deadline"],
                reported_by=pv_user.id,
                status="coded" if hit else "open",
                sae_confirmed=(serious == "serious"),
                dsmb_flag=(serious == "serious"),
                narrative=(
                    f"Subject {p.screening_number} reported {desc.lower()} "
                    f"on day {(onset - p.enrollment_date).days} post-enrollment."
                ),
            )
            if hit:
                ae.meddra_code = hit["code"]
                ae.meddra_pt = hit["pt"]
                ae.meddra_soc = hit["soc"]
                ae.coding_dictionary = "AIIA-SYNTHETIC-MedDRA-STUB v1.0"
                ae.coded_at = report_time
            db.add(ae)


def _create_deviations(db: Session, study: ResearchStudy, patients: list[Patient],
                       coordinator: User) -> None:
    if not patients:
        return
    for j in range(min(3, len(patients))):
        p = patients[j]
        desc, category, sev = DEVIATIONS[j % len(DEVIATIONS)]
        db.add(
            ProtocolDeviation(
                study_id=study.id,
                patient_id=p.id,
                deviation_number=f"{study.protocol_number}-PD{j + 1:04d}",
                description=desc,
                category=category,
                severity=sev,
                reported_by=coordinator.id,
                reported_date=date.today() - timedelta(days=j * 5),
                status="open",
            )
        )


def _write_creation_audit(db: Session, admin: User, entity_type: str,
                          entity_id: str, snapshot: dict, reason: str) -> None:
    log_action(
        db, action="SEED_CREATE", entity_type=entity_type,
        entity_id=str(entity_id), user=admin,
        new_value=snapshot, reason=reason, ip_address="127.0.0.1",
        commit=False,
    )


def run() -> dict:
    logger.info("Initialising database schema + immutability triggers")
    init_db()
    ensure_default_thresholds()

    db = SessionLocal()
    try:
        # sites
        sites = [_upsert_site(db, *s) for s in SITES]

        # users
        admin = _upsert_user(db, *USERS[5][:4])
        pi = _upsert_user(db, *USERS[0][:4], site_id=sites[0].id)
        coordinator = _upsert_user(db, *USERS[1][:4], site_id=sites[0].id)
        monitor = _upsert_user(db, *USERS[2][:4], site_id=sites[0].id)
        ec = _upsert_user(db, *USERS[3][:4])
        pv = _upsert_user(db, *USERS[4][:4])
        regulator = _upsert_user(db, *USERS[6][:4])
        db.commit()

        # studies + patients + visits + AEs + deviations
        study_summaries = []
        for spec in AYUR_STUDIES:
            spec = dict(spec)
            study = _create_study(db, spec, sites[0], pi, coordinator, monitor, admin)
            db.commit()

            patients = _create_patients(db, study, pi, coordinator, count=20)
            db.commit()
            _create_visits(db, study, patients)
            _create_aes(db, study, patients, pv)
            _create_deviations(db, study, patients, coordinator)
            db.commit()

            _write_creation_audit(
                db, admin, "ResearchStudy", study.id,
                {"protocol_number": study.protocol_number, "title": study.title,
                 "status": study.status},
                reason="Seeded synthetic Ayurveda trial",
            )
            db.commit()
            study_summaries.append({
                "protocol_number": study.protocol_number,
                "status": study.status,
                "patients": len(patients),
            })

        # one login audit per role so the audit trail is non-trivial
        for user in (admin, pi, coordinator, monitor, ec, pv, regulator):
            log_action(
                db, action="LOGIN", entity_type="User", entity_id=user.id,
                user=user, new_value={"email": user.email, "role": user.role},
                ip_address="127.0.0.1", commit=False,
            )
        db.commit()

        # first Merkle anchor
        from app.anchoring import commit_batch
        anchor = commit_batch(db, force=True)
        logger.info("Anchor result: %s", anchor["reason"])

        summary = {
            "sites": len(sites),
            "users": len(USERS),
            "studies": study_summaries,
            "credentials": {
                "password_for_all_demo_users": DEMO_PASSWORD,
                "logins": {u[2]: u[1] for u in USERS},
            },
            "first_anchor": anchor.get("anchor"),
        }
        logger.info("Seed complete: %s", summary)
        return summary
    finally:
        db.close()


if __name__ == "__main__":
    result = run()
    print("\n=== AIIA CTMS SEED SUMMARY ===")
    print(f"Password for every demo login: {result['credentials']['password_for_all_demo_users']}")
    for role, email in result["credentials"]["logins"].items():
        print(f"  {role:12s}  {email}")
    print("\nStudies seeded:")
    for s in result["studies"]:
        print(f"  {s['protocol_number']:22s}  status={s['status']:15s} patients={s['patients']}")
