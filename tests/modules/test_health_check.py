import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.core.session_auth import SESSION_COOKIE_NAME
from app.modules.health_check import checks

_USER = {"uid": "u1", "email": "admin@example.com"}
_MEMBER = {"uid": "u1", "role": "owner", "email": "admin@example.com"}
_ENVIRONMENT = {"id": "env1", "tenant_id": "t1", "display_name": "Prod"}
_PREFIX = "/t/t1/e/env1"


def _client():
    c = TestClient(app, follow_redirects=False)
    c.cookies.set(SESSION_COOKIE_NAME, "fake-cookie")
    return c


def _auth_mocks():
    return (
        patch("app.core.session_auth.verify_session_cookie", return_value=_USER),
        patch("app.core.tenants.get_member", return_value=_MEMBER),
        patch("app.core.tenants.get_environment", return_value=_ENVIRONMENT),
    )


# ---------------------------------------------------------------------------
# Individual check functions
# ---------------------------------------------------------------------------
def test_check_iam_roles_pass(monkeypatch):
    monkeypatch.setattr(settings, "RUNTIME_SERVICE_ACCOUNT_EMAIL", "sa@proj.iam.gserviceaccount.com")
    bindings = [{"role": r, "members": ["serviceAccount:sa@proj.iam.gserviceaccount.com"]}
                for r in checks._BASELINE_SA_ROLES]
    with patch("app.modules.health_check.checks.iam_client.get_project_iam_bindings", return_value=bindings):
        result = checks.check_iam_roles()
    assert result["status"] == "pass"


def test_check_iam_roles_fail_when_missing(monkeypatch):
    monkeypatch.setattr(settings, "RUNTIME_SERVICE_ACCOUNT_EMAIL", "sa@proj.iam.gserviceaccount.com")
    with patch("app.modules.health_check.checks.iam_client.get_project_iam_bindings", return_value=[]):
        result = checks.check_iam_roles()
    assert result["status"] == "fail"
    assert "roles/datastore.user" in result["message"]


def test_check_iam_roles_warn_on_extra_roles(monkeypatch):
    monkeypatch.setattr(settings, "RUNTIME_SERVICE_ACCOUNT_EMAIL", "sa@proj.iam.gserviceaccount.com")
    member = "serviceAccount:sa@proj.iam.gserviceaccount.com"
    bindings = [{"role": r, "members": [member]} for r in checks._BASELINE_SA_ROLES]
    bindings.append({"role": "roles/owner", "members": [member]})
    with patch("app.modules.health_check.checks.iam_client.get_project_iam_bindings", return_value=bindings):
        result = checks.check_iam_roles()
    assert result["status"] == "warn"


def test_check_iam_roles_fail_without_sa_email(monkeypatch):
    monkeypatch.setattr(settings, "RUNTIME_SERVICE_ACCOUNT_EMAIL", None)
    result = checks.check_iam_roles()
    assert result["status"] == "fail"


def test_check_self_impersonation_pass(monkeypatch):
    monkeypatch.setattr(settings, "RUNTIME_SERVICE_ACCOUNT_EMAIL", "sa@proj.iam.gserviceaccount.com")
    bindings = [{"role": "roles/iam.serviceAccountTokenCreator",
                "members": ["serviceAccount:sa@proj.iam.gserviceaccount.com"]}]
    with patch("app.modules.health_check.checks.iam_client.get_service_account_iam_bindings",
              return_value=bindings):
        result = checks.check_self_impersonation()
    assert result["status"] == "pass"


def test_check_self_impersonation_fail(monkeypatch):
    monkeypatch.setattr(settings, "RUNTIME_SERVICE_ACCOUNT_EMAIL", "sa@proj.iam.gserviceaccount.com")
    with patch("app.modules.health_check.checks.iam_client.get_service_account_iam_bindings", return_value=[]):
        result = checks.check_self_impersonation()
    assert result["status"] == "fail"
    assert "404: Domain not found" in result["guidance"]


def test_check_apis_enabled_pass(monkeypatch):
    with patch("app.modules.health_check.checks.iam_client.list_enabled_services",
              return_value=list(checks._BASELINE_SERVICES)):
        result = checks.check_apis_enabled()
    assert result["status"] == "pass"


def test_check_apis_enabled_fail_when_missing():
    with patch("app.modules.health_check.checks.iam_client.list_enabled_services", return_value=[]):
        result = checks.check_apis_enabled()
    assert result["status"] == "fail"


def test_check_license_subscription_pass():
    gem = MagicMock()
    gem.list_license_configs.return_value = [{"name": "x"}]
    with patch("app.modules.health_check.checks.GeminiLicenseClient", return_value=gem):
        result = checks.check_license_subscription({"sa_email": None, "gcp_project_id": "proj"})
    assert result["status"] == "pass"


def test_check_license_subscription_warn_when_empty():
    gem = MagicMock()
    gem.list_license_configs.return_value = []
    with patch("app.modules.health_check.checks.GeminiLicenseClient", return_value=gem):
        result = checks.check_license_subscription({"sa_email": None, "gcp_project_id": "proj"})
    assert result["status"] == "warn"


def test_check_license_subscription_fail_on_error():
    gem = MagicMock()
    gem.list_license_configs.side_effect = RuntimeError("permission denied")
    with patch("app.modules.health_check.checks.GeminiLicenseClient", return_value=gem):
        result = checks.check_license_subscription({"sa_email": None, "gcp_project_id": "proj"})
    assert result["status"] == "fail"


def test_check_dwd_connectivity_maps_success_flag():
    wc = MagicMock()
    wc.test_dwd_connection.return_value = {"success": True, "message": "ok"}
    with patch("app.modules.health_check.checks.get_config", return_value={}), \
         patch("app.modules.health_check.checks.WorkspaceClient", return_value=wc):
        result = checks.check_dwd_connectivity("t1", "env1", {"sa_email": None})
    assert result["status"] == "pass"


def test_check_firestore_pass():
    with patch("app.modules.health_check.checks.get_config", return_value={}):
        result = checks.check_firestore("t1", "env1")
    assert result["status"] == "pass"


def test_check_firestore_fail():
    with patch("app.modules.health_check.checks.get_config", side_effect=RuntimeError("no creds")):
        result = checks.check_firestore("t1", "env1")
    assert result["status"] == "fail"


def test_check_scheduler_skipped_when_license_sync_disabled():
    with patch("app.modules.health_check.checks.is_module_enabled", return_value=False):
        assert checks.check_scheduler("t1", "env1") is None


def test_check_scheduler_pass_when_available():
    sched = MagicMock()
    sched.get_schedule.return_value = {
        "available": True, "job_name": "job", "schedule": "0 2 * * *", "time_zone": "UTC",
    }
    with patch("app.modules.health_check.checks.is_module_enabled", return_value=True), \
         patch("app.modules.health_check.checks.SchedulerService", return_value=sched):
        result = checks.check_scheduler("t1", "env1")
    assert result["status"] == "pass"


def test_run_all_checks_aggregates_counts():
    passing = {"id": "x", "label": "X", "status": "pass", "message": "", "guidance": None, "checked_at": "t"}
    with patch("app.modules.health_check.checks.check_iam_roles", return_value=passing), \
         patch("app.modules.health_check.checks.check_self_impersonation", return_value=passing), \
         patch("app.modules.health_check.checks.check_apis_enabled", return_value=passing), \
         patch("app.modules.health_check.checks.check_license_subscription", return_value=passing), \
         patch("app.modules.health_check.checks.check_dwd_connectivity", return_value=passing), \
         patch("app.modules.health_check.checks.check_firestore", return_value=passing), \
         patch("app.modules.health_check.checks.check_scheduler", return_value=None):
        report = checks.run_all_checks("t1", "env1", {"sa_email": None, "gcp_project_id": "proj"})
    assert report["counts"] == {"pass": 6, "warn": 0, "fail": 0}
    assert len(report["checks"]) == 6


# ---------------------------------------------------------------------------
# Router / integration
# ---------------------------------------------------------------------------
_PASSING_REPORT = {
    "checks": [{"id": "x", "label": "X", "status": "pass", "message": "ok", "guidance": None,
               "checked_at": "2026-01-01T00:00:00+00:00"}],
    "counts": {"pass": 1, "warn": 0, "fail": 0},
    "ran_at": "2026-01-01T00:00:00+00:00",
}


def test_health_check_page_disabled_redirects():
    m1, m2, m3 = _auth_mocks()
    with m1, m2, m3, patch("app.core.module_auth.is_module_enabled", return_value=False):
        r = _client().get(f"{_PREFIX}/modules/health-check")
    assert r.status_code == 303
    assert r.headers["location"] == f"{_PREFIX}/?disabled_module=health-check"


def test_health_check_page_renders_when_enabled():
    m1, m2, m3 = _auth_mocks()
    with m1, m2, m3, \
         patch("app.core.module_auth.is_module_enabled", return_value=True), \
         patch("app.modules.health_check.router.run_all_checks", return_value=_PASSING_REPORT), \
         patch("app.modules.health_check.router.update_module_config"):
        r = _client().get(f"{_PREFIX}/modules/health-check")
    assert r.status_code == 200
    assert "Gemini Enterprise Health Check" in r.text


def test_health_check_rerun_api_disabled_403():
    m1, m2, m3 = _auth_mocks()
    with m1, m2, m3, patch("app.core.module_auth.is_module_enabled", return_value=False):
        r = _client().post(f"{_PREFIX}/modules/health-check/api/run")
    assert r.status_code == 403


def test_health_check_rerun_api_enabled_returns_report():
    m1, m2, m3 = _auth_mocks()
    with m1, m2, m3, \
         patch("app.core.module_auth.is_module_enabled", return_value=True), \
         patch("app.modules.health_check.router.run_all_checks", return_value=_PASSING_REPORT), \
         patch("app.modules.health_check.router.update_module_config"):
        r = _client().post(f"{_PREFIX}/modules/health-check/api/run")
    assert r.status_code == 200
    assert r.json()["counts"]["pass"] == 1
