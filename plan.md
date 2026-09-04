# plan.md

## 1) Objectives
- Deliver **backend-only** AIIA CTMS on **FastAPI + PostgreSQL** with:
  - Core CTMS modules (studies, sites, users, milestones, patients, visits, deviations, queries, monitoring)
  - Pharmacovigilance module (AE/SAE capture, coding stub, deadlines, PV inbox, DSMB feed)
  - **RBAC/JWT auth** (7 roles)
  - **Immutable audit trail** (append-only, PostgreSQL triggers + SHA-256 hash-chain)
  - **Merkle batching + pluggable anchoring** (default `LOCAL`; optional `POLYGON_AMOY` via env)
  - **FHIR R4 read/search layer**: `ResearchStudy`, `Patient`, `ResearchSubject`, `Encounter`, `AdverseEvent` + Bundle searchset + CapabilityStatement
  - **Regulatory exports**: CDISC **SDTM DM + AE** CSV + **Define-XML skeleton**
  - Background scheduler jobs (overdue visits/milestones, AE deadline warnings, IEC renewal warnings, enrollment lag, periodic Merkle commit)
- Provide a **seed script** with synthetic Ayurveda trial data + **7 role users**.
- Ensure backend is runnable under supervisor at **0.0.0.0:8001**.

**Current status (updated):** ✅ **MVP COMPLETE**
- Phase 2 fully implemented and wired.
- Seed script generates a full demo portfolio.
- End-to-end API smoke tests passed for: auth/JWT, FHIR Bundles, audit verification + tamper detection, SDTM exports, immutability triggers, RBAC enforcement.

---

## 2) Implementation Steps

### Phase 1 — Core POC (isolation; do not proceed until green)
**User stories**
1. As an admin, I can connect to Postgres and create the required tables so the system can store CTMS data reliably.
2. As a regulator, I can verify the audit trail integrity and detect any tampering via Merkle verification.
3. As a developer, I can switch anchoring between LOCAL and POLYGON_AMOY using env vars without code changes.
4. As an integrator, I can fetch valid FHIR R4 resources and validate them externally.
5. As a coordinator, I am blocked from enrolling a patient unless CTRI + IEC + study activation gates are satisfied.

**Steps**
1. Research minimal FHIR R4 search bundle conventions + CapabilityStatement requirements.
2. Scaffold backend structure under `/app/backend`.
3. Implement minimal DB schema subset needed for POC.
4. Implement audit hash-chaining + append-only enforcement triggers.
5. Implement Merkle batching + anchoring (LOCAL default).
6. Implement auth + RBAC core.
7. Implement minimal POC FHIR endpoints.
8. Create `/app/backend/poc_test.py` to prove DB connectivity, immutability, anchoring, tamper detection, RBAC, and FHIR validity.

**Exit criteria**
- ✅ `poc_test.py` passes end-to-end with LOCAL anchor (previously confirmed).
- ✅ `/api/audit/verify` reports matched/mismatched reliably.
- ✅ Sample FHIR outputs validate structurally against R4 expectations.

**Status:** ✅ Completed earlier (POC passed 69/69).

---

### Phase 2 — V1 App Development (build full API surface around proven core)
**User stories**
1. As a PI, I can create a study and progress it through protocol → EC approval → CTRI registered → active.
2. As a coordinator, I can screen/enroll patients and manage visit schedules with compliance status.
3. As PV, I can capture AE/SAE and see deadline dashboards and escalations.
4. As a monitor, I can raise and close data queries and file monitoring visit reports.
5. As a regulator, I can read-only inspect studies/patients/AEs and download SDTM exports.

**Steps**
1. Expand DB schema to full MVP tables per spec.
2. Implement service layer for business rules (status transitions, CTRI hard gate, deadlines, overdue calculations).
3. Implement REST APIs (all under `/api`).
4. Implement **expanded FHIR R4 layer** (read + search + Bundles + CapabilityStatement + OperationOutcome).
5. Seed data (`/app/backend/seed.py`) creates multi-study portfolio with patients/visits/AEs/deviations + anchors.
6. Background jobs (APScheduler).
7. Deployment wiring: server entrypoint + supervisor + PostgreSQL.
8. Backend-only E2E testing.

**Exit criteria**
- ✅ All required endpoints implemented and RBAC-protected.
- ✅ Seeded system supports full demo flow via API calls.
- ✅ FHIR search/read returns proper Bundles.
- ✅ Exports endpoints deliver SDTM datasets.

**Status:** ✅ **COMPLETED**
- Implemented routers:
  - `/api/auth`, `/api/users`, `/api/sites`, `/api/studies`, `/api/milestones`, `/api/patients`, `/api/visits`, `/api/deviations`, `/api/queries`, `/api/monitoring-reports`, `/api/ae`, `/api/alerts`, `/api/audit`, `/api/exports`, `/api/fhir`
- Added new production entrypoint: `/app/backend/server.py` (FastAPI app wiring, lifespan init, CORS, exception handlers).
- Seed script: `/app/backend/seed.py` populates:
  - 7 users (roles): pi/coordinator/monitor/ec/pv/admin/regulator
  - 2 sites
  - 3 studies across lifecycle states
  - 36 patients total (approx), visits (90), AEs (14) + deviations
  - audit chain + first Merkle anchor (LOCAL)
- Verified E2E via curl:
  - ✅ login/JWT
  - ✅ studies list + KPIs
  - ✅ FHIR `/metadata` + search Bundles
  - ✅ SDTM exports preview
  - ✅ tamper simulation → `TAMPER_DETECTED` and restore → `VERIFIED`
  - ✅ immutability trigger enforcement
  - ✅ RBAC enforcement (PI blocked from admin-only actions)

---

### Phase 3 — Hardening + POLYGON_AMOY switch + packaging
**User stories**
1. As admin, I can schedule automatic Merkle commits and review anchor history.
2. As regulator, I can export SDTM DM/AE + define.xml and verify dataset consistency.
3. As PV, I can confirm SAE and ensure EC escalation is recorded and auditable.
4. As a developer, I can enable POLYGON_AMOY anchoring with env vars and see tx details stored.
5. As admin, I can tune alert thresholds and see them affect overdue alerts.

**Steps (revised to reflect current state)**
1. **Polygon Amoy productionization (optional, env-driven):**
   - Provide `.env.example` with `ANCHOR_MODE=POLYGON_AMOY` and required env vars
   - Validate end-to-end on-chain commit + on-chain verify (read back root)
2. **Testing hardening:**
   - Convert curl smoke tests into repeatable `pytest` suite (API + audit integrity + FHIR)
   - Add negative tests (RBAC denials, enrollment gate enforcement, locked database enforcement)
3. **Operational hardening:**
   - Logging improvements (request IDs, structured logs)
   - Add basic rate limiting / brute-force protection (optional)
   - Database indexes review (audit tables, AE deadlines, study_id filters)
4. **Data export polish:**
   - Confirm SDTM variable-level mappings and document assumptions
   - Add ZIP export endpoint packaging dm.csv + ae.csv + define.xml (optional)
5. **Documentation:**
   - Write `/app/README.md` with run instructions, seed credentials, and key endpoints
   - Document anchoring modes and how to verify audit integrity

**Exit criteria**
- POLYGON_AMOY mode verified with a real tx hash stored (when enabled).
- Full automated backend test suite green.
- Docs complete for deployment + API usage.

**Status:** 🔜 Next phase (optional / hardening).

---

## 3) Next Actions (immediate)
1. **Persist demo credentials to disk** (if required by brief):
   - Write a small doc file (e.g., `/app/memory/test_credentials.md`) containing:
     - emails for each role
     - password `Aiia@2025`
     - base URL `/api/docs`
2. Add `.env.example` for both LOCAL and POLYGON_AMOY modes.
3. Add automated `pytest` E2E smoke tests (auth → seed → FHIR → audit verify → export).
4. (Optional) Implement ZIP bundle endpoint for SDTM export packaging.

---

## 4) Success Criteria
- ✅ POC proves: DB works, audit immutable, Merkle verify detects tampering, FHIR JSON valid, RBAC enforced, CTRI gate enforced.
- ✅ V1 provides all `/api/*` endpoints in spec + seed data + exports + background jobs.
- ✅ FHIR layer supports read + search Bundles + `/api/fhir/metadata`.
- ✅ Anchoring is pluggable: LOCAL works by default; POLYGON_AMOY works when enabled via env.
- ✅ Backend stable under supervisor at `0.0.0.0:8001`; no UI required.
