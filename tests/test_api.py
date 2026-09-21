import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient
from app.main import app
from app.core.session_auth import SESSION_COOKIE_NAME

client = TestClient(app, follow_redirects=False)

_USER = {"uid": "u1", "email": "boss@example.com"}
_MEMBER = {"uid": "u1", "role": "owner", "email": "boss@example.com"}
_ENVIRONMENT = {"id": "env1", "tenant_id": "t1", "display_name": "Prod"}

_PREFIX = "/t/t1/e/env1"


def _signed_in_client():
    c = TestClient(app, follow_redirects=False)
    c.cookies.set(SESSION_COOKIE_NAME, "fake-cookie")
    return c


def _auth_mocks():
    """The three dependencies every environment-scoped route resolves through,
    ahead of any module-specific logic. license-sync's own `is_module_enabled`
    check is left unmocked deliberately - it fails open to its registry default
    (True) when Firestore isn't reachable, exactly like it would for a fresh,
    never-toggled environment."""
    return (
        patch("app.core.session_auth.verify_session_cookie", return_value=_USER),
        patch("app.core.tenants.get_member", return_value=_MEMBER),
        patch("app.core.tenants.get_environment", return_value=_ENVIRONMENT),
    )


def test_unauthenticated_blocks_access():
    """No session cookie: pages and mutating APIs 401."""
    assert client.get(f"{_PREFIX}/modules/license-sync").status_code == 401
    assert client.post(f"{_PREFIX}/modules/license-sync/api/settings", json={
        "delegated_admin_email": "a@b.com", "license_config": ""
    }).status_code == 401
    # health probe stays open for Cloud Run
    assert client.get("/healthz").status_code == 200


def test_non_member_blocked_with_403():
    m1, m2, m3 = _auth_mocks()
    with m1, patch("app.core.tenants.get_member", return_value=None):
        r = _signed_in_client().get(f"{_PREFIX}/modules/license-sync")
    assert r.status_code == 403


def test_authenticated_member_sees_principal_in_nav():
    m1, m2, m3 = _auth_mocks()
    mock_config = {
        "monitored_groups": [], "license_config": "", "license_label": "",
        "delegated_admin_email": "admin@example.com", "cron_expression": "0 2 * * *",
        "notification_emails": [], "notify_on": "failures",
    }
    with m1, m2, m3, \
         patch("app.modules.license_sync.router.get_config", return_value=mock_config), \
         patch("app.modules.license_sync.router.get_sync_history", return_value=[]):
        r = _signed_in_client().get(f"{_PREFIX}/modules/license-sync")
    assert r.status_code == 200
    assert "boss@example.com" in r.text


def test_dashboard_route():
    """Verify the License Sync dashboard renders HTML."""
    mock_config = {
        "monitored_groups": ["team@example.com"],
        "license_config": "",
        "license_label": "",
        "delegated_admin_email": "admin@example.com",
        "cron_expression": "0 2 * * *"
    }
    m1, m2, m3 = _auth_mocks()
    with m1, m2, m3, \
         patch("app.modules.license_sync.router.get_config", return_value=mock_config), \
         patch("app.modules.license_sync.router.get_sync_history", return_value=[]):
        response = _signed_in_client().get(f"{_PREFIX}/modules/license-sync")
        assert response.status_code == 200
        assert "Gemini License Provisioning Dashboard" in response.text
        assert "team@example.com" in response.text


def test_api_save_groups():
    """Verify saving monitored groups via API."""
    m1, m2, m3 = _auth_mocks()
    with m1, m2, m3, \
         patch("app.modules.license_sync.router.update_config",
               return_value={"monitored_groups": ["group1@domain.com"]}):
        response = _signed_in_client().post(
            f"{_PREFIX}/modules/license-sync/api/groups",
            json={"groups": ["group1@domain.com"]}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["monitored_groups"] == ["group1@domain.com"]


_LC = "projects/750/locations/us/licenseConfigs/gemini_ent"


def test_api_save_settings_with_valid_subscription():
    gem = MagicMock()
    gem.list_license_configs.return_value = [{"name": _LC, "label": "Gemini Enterprise — us"}]
    m1, m2, m3 = _auth_mocks()
    with m1, m2, m3, \
         patch("app.modules.license_sync.router.update_config", return_value={}), \
         patch("app.modules.license_sync.router.GeminiLicenseClient", return_value=gem):
        response = _signed_in_client().post(f"{_PREFIX}/modules/license-sync/api/settings", json={
            "delegated_admin_email": "admin@test.com", "license_config": _LC,
        })
        assert response.status_code == 200
        assert response.json()["success"] is True


def test_api_save_settings_rejects_workspace_sku():
    m1, m2, m3 = _auth_mocks()
    with m1, m2, m3, patch("app.modules.license_sync.router.update_config", return_value={}):
        response = _signed_in_client().post(f"{_PREFIX}/modules/license-sync/api/settings", json={
            "delegated_admin_email": "admin@test.com", "license_config": "Google-Apps",
        })
        assert response.status_code == 400


def test_api_save_settings_rejects_unknown_subscription():
    gem = MagicMock()
    gem.list_license_configs.return_value = [{"name": _LC, "label": "x"}]
    m1, m2, m3 = _auth_mocks()
    with m1, m2, m3, \
         patch("app.modules.license_sync.router.update_config", return_value={}), \
         patch("app.modules.license_sync.router.GeminiLicenseClient", return_value=gem):
        response = _signed_in_client().post(f"{_PREFIX}/modules/license-sync/api/settings", json={
            "delegated_admin_email": "admin@test.com",
            "license_config": "projects/750/locations/us/licenseConfigs/other",
        })
        assert response.status_code == 400


def test_settings_view_renders_subscription_dropdown():
    mock_config = {
        "monitored_groups": [], "license_config": _LC, "license_label": "Gemini Enterprise — us",
        "delegated_admin_email": "admin@example.com", "cron_expression": "0 2 * * *",
    }
    gem = MagicMock()
    gem.list_license_configs.return_value = [
        {"name": _LC, "label": "Gemini Enterprise — 50 seats — us — Free trial [ACTIVE]"},
    ]
    m1, m2, m3 = _auth_mocks()
    with m1, m2, m3, \
         patch("app.modules.license_sync.router.get_config", return_value=mock_config), \
         patch("app.modules.license_sync.router.GeminiLicenseClient", return_value=gem):
        r = _signed_in_client().get(f"{_PREFIX}/modules/license-sync/settings")
        assert r.status_code == 200
        assert "Gemini Enterprise License Subscription" in r.text
        assert "Free trial [ACTIVE]" in r.text
        assert "Product ID" not in r.text and "SKU ID" not in r.text


def test_settings_view_handles_license_api_error():
    mock_config = {"monitored_groups": [], "license_config": "", "license_label": "",
                   "delegated_admin_email": "a@e.com", "cron_expression": "0 2 * * *"}
    gem = MagicMock()
    gem.list_license_configs.side_effect = RuntimeError("permission denied")
    m1, m2, m3 = _auth_mocks()
    with m1, m2, m3, \
         patch("app.modules.license_sync.router.get_config", return_value=mock_config), \
         patch("app.modules.license_sync.router.GeminiLicenseClient", return_value=gem):
        r = _signed_in_client().get(f"{_PREFIX}/modules/license-sync/settings")
        assert r.status_code == 200
        assert "Could not list license subscriptions" in r.text


def test_schedule_view_renders_notification_settings():
    """The Sync Schedule page shows saved notification recipients + mode."""
    mock_config = {
        "monitored_groups": [],
        "license_config": "",
        "license_label": "",
        "delegated_admin_email": "admin@example.com",
        "cron_expression": "0 2 * * *",
        "notification_emails": ["ops@example.com"],
        "notify_on": "all",
    }
    sched_status = {"available": False, "job_name": "job", "schedule": None, "time_zone": "UTC"}
    m1, m2, m3 = _auth_mocks()
    with m1, m2, m3, \
         patch("app.modules.license_sync.router.get_config", return_value=mock_config), \
         patch("app.modules.license_sync.router.SchedulerService") as MockSched:
        MockSched.return_value.get_schedule.return_value = sched_status
        response = _signed_in_client().get(f"{_PREFIX}/modules/license-sync/schedule")
        assert response.status_code == 200
        assert "Run Notifications" in response.text
        assert "ops@example.com" in response.text
        assert "checkbox" in response.text and "notify-all" in response.text


def test_api_save_notifications_valid():
    """Save notification recipients + mode via API."""
    m1, m2, m3 = _auth_mocks()
    with m1, m2, m3, \
         patch("app.modules.license_sync.router.update_config", return_value={
             "notification_emails": ["ops@example.com", "sre@example.com"],
             "notify_on": "all",
         }):
        response = _signed_in_client().post(
            f"{_PREFIX}/modules/license-sync/api/notifications",
            json={"notification_emails": "ops@example.com, sre@example.com", "notify_on": "all"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["notify_on"] == "all"
        assert "ops@example.com" in data["notification_emails"]


def test_api_save_notifications_rejects_bad_email():
    m1, m2, m3 = _auth_mocks()
    with m1, m2, m3:
        response = _signed_in_client().post(
            f"{_PREFIX}/modules/license-sync/api/notifications",
            json={"notification_emails": ["not-an-email"], "notify_on": "failures"},
        )
        assert response.status_code == 400


def test_api_save_notifications_rejects_bad_mode():
    m1, m2, m3 = _auth_mocks()
    with m1, m2, m3:
        response = _signed_in_client().post(
            f"{_PREFIX}/modules/license-sync/api/notifications",
            json={"notification_emails": [], "notify_on": "sometimes"},
        )
        assert response.status_code == 400


def test_api_test_connection():
    """Verify DWD test connection endpoint returns result structure."""
    mock_res = {
        "success": True,
        "message": "Connected",
        "subject": "admin@test.com"
    }
    m1, m2, m3 = _auth_mocks()
    with m1, m2, m3, patch("app.modules.license_sync.router.WorkspaceClient") as MockClient:
        mock_instance = MagicMock()
        mock_instance.test_dwd_connection.return_value = mock_res
        MockClient.return_value = mock_instance

        response = _signed_in_client().post(
            f"{_PREFIX}/modules/license-sync/api/test-connection",
            json={"delegated_admin_email": "admin@test.com"}
        )
        assert response.status_code == 200
        assert response.json()["success"] is True
