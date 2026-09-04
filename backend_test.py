"""
AIIA CTMS Backend Test Suite
Tests all endpoints after code review fixes:
- DEMO_PASSWORD env var
- Unused imports removed
- Type hints added
- FHIR mapping refactors (research_study_to_fhir, adverse_event_to_fhir)
"""
import requests
import sys
from datetime import datetime

BASE_URL = "https://db-postgres-api.preview.emergentagent.com/api"

class CTMSBackendTester:
    def __init__(self):
        self.base_url = BASE_URL
        self.admin_token = None
        self.pi_token = None
        self.tests_run = 0
        self.tests_passed = 0
        self.failed_tests = []
        self.active_study_id = None

    def log(self, message, level="INFO"):
        """Log test messages"""
        print(f"[{level}] {message}")

    def run_test(self, name, method, endpoint, expected_status, data=None, headers=None, params=None):
        """Run a single API test"""
        url = f"{self.base_url}/{endpoint}"
        if headers is None:
            headers = {'Content-Type': 'application/json'}
        
        self.tests_run += 1
        self.log(f"\n🔍 Test {self.tests_run}: {name}")
        
        try:
            if method == 'GET':
                response = requests.get(url, headers=headers, params=params, timeout=30)
            elif method == 'POST':
                response = requests.post(url, json=data, headers=headers, params=params, timeout=30)
            elif method == 'PUT':
                response = requests.put(url, json=data, headers=headers, timeout=30)
            elif method == 'DELETE':
                response = requests.delete(url, headers=headers, timeout=30)
            else:
                raise ValueError(f"Unsupported method: {method}")

            success = response.status_code == expected_status
            if success:
                self.tests_passed += 1
                self.log(f"✅ PASSED - Status: {response.status_code}", "PASS")
                try:
                    return True, response.json()
                except:
                    return True, response.text
            else:
                self.log(f"❌ FAILED - Expected {expected_status}, got {response.status_code}", "FAIL")
                self.log(f"Response: {response.text[:500]}", "FAIL")
                self.failed_tests.append({
                    "test": name,
                    "expected": expected_status,
                    "actual": response.status_code,
                    "response": response.text[:500]
                })
                return False, {}

        except Exception as e:
            self.log(f"❌ FAILED - Error: {str(e)}", "FAIL")
            self.failed_tests.append({
                "test": name,
                "error": str(e)
            })
            return False, {}

    def test_auth_login_admin(self):
        """Test admin login with DEMO_PASSWORD from env"""
        success, response = self.run_test(
            "Auth: Admin Login",
            "POST",
            "auth/login",
            200,
            data={"email": "admin@aiia.gov.in", "password": "Aiia@2025"}
        )
        if success and 'access_token' in response:
            self.admin_token = response['access_token']
            self.log(f"Admin token obtained: {self.admin_token[:20]}...", "INFO")
            return True
        return False

    def test_auth_login_pi(self):
        """Test PI login"""
        success, response = self.run_test(
            "Auth: PI Login",
            "POST",
            "auth/login",
            200,
            data={"email": "pi@aiia.gov.in", "password": "Aiia@2025"}
        )
        if success and 'access_token' in response:
            self.pi_token = response['access_token']
            self.log(f"PI token obtained: {self.pi_token[:20]}...", "INFO")
            return True
        return False

    def test_auth_me(self):
        """Test GET /api/auth/me returns user profile"""
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.admin_token}'
        }
        success, response = self.run_test(
            "Auth: Get Current User Profile",
            "GET",
            "auth/me",
            200,
            headers=headers
        )
        if success:
            self.log(f"User profile: {response.get('email')} - {response.get('role')}", "INFO")
        return success

    def test_rbac_pi_forbidden(self):
        """Test RBAC: PI should get 403 on admin-only /api/users endpoint"""
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.pi_token}'
        }
        success, response = self.run_test(
            "RBAC: PI accessing admin-only /users endpoint (expect 403)",
            "GET",
            "users",
            403,
            headers=headers
        )
        return success

    def test_studies_list(self):
        """Test GET /api/studies returns seeded studies"""
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.admin_token}'
        }
        success, response = self.run_test(
            "Studies: List all studies",
            "GET",
            "studies",
            200,
            headers=headers
        )
        if success:
            total = response.get('total', 0)
            items = response.get('items', [])
            self.log(f"Found {total} studies, {len(items)} in response", "INFO")
            if len(items) >= 3:
                # Store first active study for later tests
                for study in items:
                    if study.get('status') == 'active':
                        self.active_study_id = study.get('id')
                        self.log(f"Active study found: {self.active_study_id}", "INFO")
                        break
                return True
            else:
                self.log(f"Expected at least 3 studies, got {len(items)}", "FAIL")
                return False
        return False

    def test_study_kpis(self):
        """Test GET /api/studies/{id}/kpis"""
        if not self.active_study_id:
            self.log("No active study ID available, skipping KPI test", "WARN")
            return False
        
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.admin_token}'
        }
        success, response = self.run_test(
            f"Studies: Get KPIs for study {self.active_study_id}",
            "GET",
            f"studies/{self.active_study_id}/kpis",
            200,
            headers=headers
        )
        if success:
            self.log(f"KPIs: enrollment={response.get('enrollment')}, safety={response.get('safety')}", "INFO")
        return success

    def test_fhir_metadata(self):
        """Test FHIR CapabilityStatement"""
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.admin_token}'
        }
        success, response = self.run_test(
            "FHIR: GET /metadata (CapabilityStatement)",
            "GET",
            "fhir/metadata",
            200,
            headers=headers
        )
        if success:
            resource_type = response.get('resourceType')
            resources = response.get('rest', [{}])[0].get('resource', [])
            self.log(f"CapabilityStatement resourceType: {resource_type}, resources: {len(resources)}", "INFO")
            if resource_type == 'CapabilityStatement' and len(resources) == 5:
                return True
            else:
                self.log(f"Expected 5 resources, got {len(resources)}", "FAIL")
                return False
        return False

    def test_fhir_research_study_search(self):
        """Test FHIR ResearchStudy search - VERIFY refactored research_study_to_fhir"""
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.admin_token}'
        }
        success, response = self.run_test(
            "FHIR: GET /ResearchStudy?_count=5 (Bundle)",
            "GET",
            "fhir/ResearchStudy",
            200,
            headers=headers,
            params={'_count': 5}
        )
        if success:
            total = response.get('total', 0)
            entries = response.get('entry', [])
            self.log(f"ResearchStudy Bundle: total={total}, entries={len(entries)}", "INFO")
            
            # Verify refactored structure
            if entries:
                first_study = entries[0].get('resource', {})
                # Check for fields produced by helper functions
                has_identifier = 'identifier' in first_study and isinstance(first_study['identifier'], list)
                has_phase = 'phase' in first_study
                has_category = 'category' in first_study
                has_condition = 'condition' in first_study
                has_extension = 'extension' in first_study and isinstance(first_study['extension'], list)
                
                self.log(f"ResearchStudy structure check: identifier={has_identifier}, phase={has_phase}, category={has_category}, condition={has_condition}, extension={has_extension}", "INFO")
                
                # Check extension fields from _study_extensions
                if has_extension:
                    ext_urls = [e.get('url', '').split('/')[-1] for e in first_study['extension']]
                    self.log(f"Extension fields: {ext_urls}", "INFO")
                    expected_exts = ['ctms-status', 'enrollment-target', 'enrolled-count']
                    has_required_exts = all(ext in ext_urls for ext in expected_exts)
                    if not has_required_exts:
                        self.log(f"Missing expected extensions: {expected_exts}", "FAIL")
                        return False
                
                return total == 3 and has_identifier and has_extension
            return False
        return False

    def test_fhir_research_study_read(self):
        """Test FHIR ResearchStudy read by ID"""
        if not self.active_study_id:
            self.log("No active study ID available, skipping", "WARN")
            return False
        
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.admin_token}'
        }
        success, response = self.run_test(
            f"FHIR: GET /ResearchStudy/{self.active_study_id}",
            "GET",
            f"fhir/ResearchStudy/{self.active_study_id}",
            200,
            headers=headers
        )
        if success:
            resource_type = response.get('resourceType')
            self.log(f"ResearchStudy resource: {resource_type}, id={response.get('id')}", "INFO")
            return resource_type == 'ResearchStudy'
        return False

    def test_fhir_patient_search_gender(self):
        """Test FHIR Patient search by gender"""
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.admin_token}'
        }
        success, response = self.run_test(
            "FHIR: GET /Patient?gender=female",
            "GET",
            "fhir/Patient",
            200,
            headers=headers,
            params={'gender': 'female'}
        )
        if success:
            total = response.get('total', 0)
            entries = response.get('entry', [])
            self.log(f"Female patients: total={total}, entries={len(entries)}", "INFO")
            return total > 0
        return False

    def test_fhir_adverse_event_search(self):
        """Test FHIR AdverseEvent search - VERIFY refactored adverse_event_to_fhir"""
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.admin_token}'
        }
        success, response = self.run_test(
            "FHIR: GET /AdverseEvent?seriousness=serious",
            "GET",
            "fhir/AdverseEvent",
            200,
            headers=headers,
            params={'seriousness': 'serious'}
        )
        if success:
            total = response.get('total', 0)
            entries = response.get('entry', [])
            self.log(f"Serious AdverseEvents: total={total}, entries={len(entries)}", "INFO")
            
            # Verify refactored structure
            if entries:
                first_ae = entries[0].get('resource', {})
                # Check for fields produced by helper functions
                has_event = 'event' in first_ae
                has_seriousness = 'seriousness' in first_ae
                has_severity = 'severity' in first_ae
                has_outcome = 'outcome' in first_ae
                has_extension = 'extension' in first_ae and isinstance(first_ae['extension'], list)
                
                self.log(f"AdverseEvent structure check: event={has_event}, seriousness={has_seriousness}, severity={has_severity}, outcome={has_outcome}, extension={has_extension}", "INFO")
                
                return total > 0 and has_event and has_seriousness and has_extension
            return total > 0
        return False

    def test_audit_verify(self):
        """Test audit trail verification"""
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.admin_token}'
        }
        success, response = self.run_test(
            "Audit: GET /audit/verify",
            "GET",
            "audit/verify",
            200,
            headers=headers
        )
        if success:
            overall_status = response.get('overall_status')
            chain_intact = response.get('chain_intact')
            self.log(f"Audit verification: status={overall_status}, chain_intact={chain_intact}", "INFO")
            return overall_status == 'VERIFIED' and chain_intact == True
        return False

    def test_audit_tamper_simulate(self):
        """Test tamper detection"""
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.admin_token}'
        }
        
        # Simulate tamper
        success, response = self.run_test(
            "Audit: POST /audit/simulate-tamper",
            "POST",
            "audit/simulate-tamper",
            200,
            data={"audit_id": 1, "new_action": "HACKED"},
            headers=headers
        )
        if not success:
            return False
        
        self.log(f"Tamper simulated: {response.get('tampered')}", "INFO")
        
        # Verify tamper detected
        success, response = self.run_test(
            "Audit: GET /audit/verify (after tamper)",
            "GET",
            "audit/verify",
            200,
            headers=headers
        )
        if success:
            overall_status = response.get('overall_status')
            self.log(f"After tamper: status={overall_status}", "INFO")
            if overall_status != 'TAMPER_DETECTED':
                self.log(f"Expected TAMPER_DETECTED, got {overall_status}", "FAIL")
                return False
        else:
            return False
        
        # Restore tamper
        success, response = self.run_test(
            "Audit: POST /audit/restore-tamper",
            "POST",
            "audit/restore-tamper",
            200,
            headers=headers,
            params={"audit_id": 1, "original_action": "SEED_CREATE"}
        )
        if not success:
            return False
        
        # Verify restored
        success, response = self.run_test(
            "Audit: GET /audit/verify (after restore)",
            "GET",
            "audit/verify",
            200,
            headers=headers
        )
        if success:
            overall_status = response.get('overall_status')
            chain_intact = response.get('chain_intact')
            self.log(f"After restore: status={overall_status}, chain_intact={chain_intact}", "INFO")
            return overall_status == 'VERIFIED' and chain_intact == True
        return False

    def test_audit_immutability(self):
        """Test DB immutability enforcement"""
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.admin_token}'
        }
        success, response = self.run_test(
            "Audit: GET /audit/immutability-test",
            "GET",
            "audit/immutability-test",
            200,
            headers=headers
        )
        if success:
            conclusion = response.get('conclusion')
            results = response.get('results', {})
            update_blocked = results.get('update', {}).get('blocked', False)
            delete_blocked = results.get('delete', {}).get('blocked', False)
            self.log(f"Immutability test: conclusion={conclusion}, update_blocked={update_blocked}, delete_blocked={delete_blocked}", "INFO")
            return conclusion == 'APPEND-ONLY ENFORCED' and update_blocked and delete_blocked
        return False

    def test_audit_merkle_proof(self):
        """Test Merkle inclusion proof"""
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.admin_token}'
        }
        success, response = self.run_test(
            "Audit: GET /audit/proof/5",
            "GET",
            "audit/proof/5",
            200,
            headers=headers
        )
        if success:
            proof_valid = response.get('proof_valid')
            self.log(f"Merkle proof for audit_id=5: proof_valid={proof_valid}", "INFO")
            return proof_valid == True
        return False

    def test_sdtm_export_preview(self):
        """Test SDTM export preview"""
        if not self.active_study_id:
            self.log("No active study ID available, skipping SDTM test", "WARN")
            return False
        
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.admin_token}'
        }
        success, response = self.run_test(
            f"Exports: GET /exports/studies/{self.active_study_id}/sdtm/preview",
            "GET",
            f"exports/studies/{self.active_study_id}/sdtm/preview",
            200,
            headers=headers
        )
        if success:
            dm = response.get('dm', {})
            ae = response.get('ae', {})
            dm_rows = dm.get('row_count', 0)
            ae_rows = ae.get('row_count', 0)
            self.log(f"SDTM preview: DM rows={dm_rows}, AE rows={ae_rows}", "INFO")
            return dm_rows > 0 and ae_rows > 0
        return False

    def run_all_tests(self):
        """Run all backend tests"""
        self.log("=" * 80, "INFO")
        self.log("AIIA CTMS Backend Test Suite - Code Review Verification", "INFO")
        self.log("=" * 80, "INFO")
        
        # Auth tests
        if not self.test_auth_login_admin():
            self.log("Admin login failed, cannot continue", "FAIL")
            return False
        
        if not self.test_auth_login_pi():
            self.log("PI login failed, cannot continue", "FAIL")
            return False
        
        self.test_auth_me()
        
        # RBAC test
        self.test_rbac_pi_forbidden()
        
        # Studies tests
        self.test_studies_list()
        self.test_study_kpis()
        
        # FHIR tests - verify refactored functions
        self.test_fhir_metadata()
        self.test_fhir_research_study_search()
        self.test_fhir_research_study_read()
        self.test_fhir_patient_search_gender()
        self.test_fhir_adverse_event_search()
        
        # Audit trail tests
        self.test_audit_verify()
        self.test_audit_tamper_simulate()
        self.test_audit_immutability()
        self.test_audit_merkle_proof()
        
        # SDTM export test
        self.test_sdtm_export_preview()
        
        # Print summary
        self.log("\n" + "=" * 80, "INFO")
        self.log("TEST SUMMARY", "INFO")
        self.log("=" * 80, "INFO")
        self.log(f"Total tests run: {self.tests_run}", "INFO")
        self.log(f"Tests passed: {self.tests_passed}", "PASS")
        self.log(f"Tests failed: {len(self.failed_tests)}", "FAIL")
        self.log(f"Success rate: {(self.tests_passed/self.tests_run*100):.1f}%", "INFO")
        
        if self.failed_tests:
            self.log("\nFailed tests:", "FAIL")
            for i, test in enumerate(self.failed_tests, 1):
                self.log(f"{i}. {test.get('test')}", "FAIL")
                if 'error' in test:
                    self.log(f"   Error: {test['error']}", "FAIL")
                else:
                    self.log(f"   Expected: {test.get('expected')}, Got: {test.get('actual')}", "FAIL")
        
        return len(self.failed_tests) == 0


def main():
    tester = CTMSBackendTester()
    success = tester.run_all_tests()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
