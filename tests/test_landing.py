import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient

from app.main import app
from app.core.session_auth import SESSION_COOKIE_NAME

_USER = {"uid": "u1", "email": "admin@example.com"}
_MEMBER = {"uid": "u1", "role": "owner", "email": "admin@example.com"}
_TENANT = {"id": "t1", "name": "Acme Corp", "primary_domain": "acme.com"}
_ENVIRONMENT = {"id": "env1", "tenant_id": "t1", "display_name": "Prod", "gcp_project_id": "",
                 "status": "onboarding"}


def _client(signed_in=True):
    client = TestClient(app, follow_redirects=False)
    if signed_in:
        client.cookies.set(SESSION_COOKIE_NAME, "fake-cookie")
    return client


def _signed_in():
    return patch("app.core.session_auth.verify_session_cookie", return_value=_USER)


def test_root_shows_sign_in_when_unauthenticated():
    r = _client(signed_in=False).get("/")
    assert r.status_code == 200
    assert "Sign in with Google" in r.text


def test_root_redirects_to_single_tenant():
    with _signed_in(), patch("app.core.tenants.list_tenants_for_uid", return_value=[_TENANT]):
        r = _client().get("/")
    assert r.status_code == 303
    assert r.headers["location"] == "/t/t1/"


def test_root_shows_tenant_picker_for_zero_or_many_tenants():
    with _signed_in(), patch("app.core.tenants.list_tenants_for_uid", return_value=[]):
        r = _client().get("/")
    assert r.status_code == 200
    assert "Create a New Company" in r.text

    with _signed_in(), patch("app.core.tenants.list_tenants_for_uid",
                             return_value=[_TENANT, {"id": "t2", "name": "Globex", "primary_domain": ""}]):
        r = _client().get("/")
    assert r.status_code == 200
    assert "Acme Corp" in r.text and "Globex" in r.text


def test_create_tenant():
    with _signed_in(), \
         patch("app.core.tenants.create_tenant", return_value="new-tenant-id") as mock_create, \
         patch("app.core.tenants.add_member") as mock_add_member:
        r = _client().post("/tenants", json={"name": "Acme Corp", "primary_domain": "acme.com"})
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True
    assert data["tenant_id"] == "new-tenant-id"
    mock_create.assert_called_once()
    mock_add_member.assert_called_once_with("new-tenant-id", "u1", "admin@example.com",
                                             role="owner", added_by_uid="u1")


def test_create_tenant_requires_name():
    with _signed_in():
        r = _client().post("/tenants", json={"name": "  "})
    assert r.status_code == 400


def test_tenant_view_redirects_to_single_environment():
    with _signed_in(), \
         patch("app.core.tenants.get_member", return_value=_MEMBER), \
         patch("app.core.tenants.get_tenant", return_value=_TENANT), \
         patch("app.core.tenants.list_environments", return_value=[_ENVIRONMENT]):
        r = _client().get("/t/t1/")
    assert r.status_code == 303
    assert r.headers["location"] == "/t/t1/e/env1/"


def test_tenant_view_shows_environment_picker_when_none():
    with _signed_in(), \
         patch("app.core.tenants.get_member", return_value=_MEMBER), \
         patch("app.core.tenants.get_tenant", return_value=_TENANT), \
         patch("app.core.tenants.list_environments", return_value=[]):
        r = _client().get("/t/t1/")
    assert r.status_code == 200
    assert "Create a New Environment" in r.text


def test_tenant_view_403_for_non_member():
    with _signed_in(), patch("app.core.tenants.get_member", return_value=None):
        r = _client().get("/t/t1/")
    assert r.status_code == 403


def test_create_environment():
    with _signed_in(), \
         patch("app.core.tenants.get_member", return_value=_MEMBER), \
         patch("app.core.tenants.create_environment", return_value="new-env-id") as mock_create:
        r = _client().post("/t/t1/environments", json={"display_name": "Prod"})
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True
    assert data["environment_id"] == "new-env-id"
    mock_create.assert_called_once_with("t1", "Prod")


def test_environment_landing_lists_all_modules():
    with _signed_in(), \
         patch("app.core.tenants.get_member", return_value=_MEMBER), \
         patch("app.core.tenants.get_environment", return_value=_ENVIRONMENT), \
         patch("app.core.module_config.get_module_config", return_value={"enabled": False}):
        r = _client().get("/t/t1/e/env1/")
    assert r.status_code == 200
    for title in ("License Sync", "App URL Mapping", "Agent Deployment", "Health Check"):
        assert title in r.text


def test_toggle_module_enables_and_disables():
    with _signed_in(), \
         patch("app.core.tenants.get_member", return_value=_MEMBER), \
         patch("app.core.tenants.get_environment", return_value=_ENVIRONMENT), \
         patch("app.landing.update_module_config") as mock_update:
        mock_update.return_value = {"enabled": True}
        r = _client().post("/t/t1/e/env1/api/modules/health-check/toggle", json={"enabled": True})
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True
    assert data["module_id"] == "health-check"
    assert data["enabled"] is True
    mock_update.assert_called_once()
    args, kwargs = mock_update.call_args
    assert args[0] == "t1" and args[1] == "env1" and args[2] == "health-check"
    assert args[3] == {"enabled": True}


def test_toggle_unknown_module_404():
    with _signed_in(), \
         patch("app.core.tenants.get_member", return_value=_MEMBER), \
         patch("app.core.tenants.get_environment", return_value=_ENVIRONMENT):
        r = _client().post("/t/t1/e/env1/api/modules/does-not-exist/toggle", json={"enabled": True})
    assert r.status_code == 404


def test_toggle_requires_authentication():
    r = _client(signed_in=False).post("/t/t1/e/env1/api/modules/health-check/toggle", json={"enabled": True})
    assert r.status_code == 401
