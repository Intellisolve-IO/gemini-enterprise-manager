import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException, Request

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import auth
from app.config import settings


def _request(headers=None):
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
    }
    return Request(scope)


@pytest.fixture(autouse=True)
def _clear_cache():
    auth._admin_cache.clear()
    yield
    auth._admin_cache.clear()


def _dir_service(is_admin=True, suspended=False):
    svc = MagicMock()
    svc.users.return_value.get.return_value.execute.return_value = {
        "isAdmin": is_admin, "suspended": suspended
    }
    return svc


def test_is_super_admin_true_for_active_admin():
    with patch("app.auth.WorkspaceClient") as MC:
        MC.return_value.get_directory_service.return_value = _dir_service(True, False)
        assert auth.is_super_admin("Boss@Example.com") is True


def test_is_super_admin_false_for_non_admin_or_suspended():
    with patch("app.auth.WorkspaceClient") as MC:
        MC.return_value.get_directory_service.return_value = _dir_service(False, False)
        assert auth.is_super_admin("user@example.com") is False
    auth._admin_cache.clear()
    with patch("app.auth.WorkspaceClient") as MC:
        MC.return_value.get_directory_service.return_value = _dir_service(True, True)
        assert auth.is_super_admin("suspended@example.com") is False


def test_is_super_admin_uses_cache():
    with patch("app.auth.WorkspaceClient") as MC:
        MC.return_value.get_directory_service.return_value = _dir_service(True, False)
        assert auth.is_super_admin("a@example.com") is True
        assert auth.is_super_admin("a@example.com") is True
        assert MC.call_count == 1  # second call served from cache


def test_is_super_admin_fails_closed_on_lookup_error():
    with patch("app.auth.WorkspaceClient", side_effect=RuntimeError("directory down")):
        assert auth.is_super_admin("a@example.com") is False


def test_bootstrap_admins_bypass_directory(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_BOOTSTRAP_ADMINS", "break-glass@example.com")
    with patch("app.auth.WorkspaceClient") as MC:
        assert auth.is_super_admin("break-glass@example.com") is True
        MC.assert_not_called()


def test_require_super_admin_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "IAP_AUDIENCE", None)
    assert auth.require_super_admin(_request()) is None


def test_require_super_admin_401_without_header(monkeypatch):
    monkeypatch.setattr(settings, "IAP_AUDIENCE", "aud")
    with pytest.raises(HTTPException) as ei:
        auth.require_super_admin(_request())
    assert ei.value.status_code == 401


def test_require_super_admin_403_when_not_admin(monkeypatch):
    monkeypatch.setattr(settings, "IAP_AUDIENCE", "aud")
    monkeypatch.setattr(auth, "_verify_iap_assertion", lambda a: {"email": "nope@example.com"})
    monkeypatch.setattr(auth, "is_super_admin", lambda e: False)
    with pytest.raises(HTTPException) as ei:
        auth.require_super_admin(_request({"x-goog-iap-jwt-assertion": "tok"}))
    assert ei.value.status_code == 403


def test_require_super_admin_allows_admin(monkeypatch):
    monkeypatch.setattr(settings, "IAP_AUDIENCE", "aud")
    monkeypatch.setattr(auth, "_verify_iap_assertion", lambda a: {"email": "Boss@example.com"})
    monkeypatch.setattr(auth, "is_super_admin", lambda e: True)
    assert auth.require_super_admin(_request({"x-goog-iap-jwt-assertion": "tok"})) == "boss@example.com"


def test_require_sync_caller_allows_scheduler_sa(monkeypatch):
    monkeypatch.setattr(settings, "IAP_AUDIENCE", "aud")
    monkeypatch.setattr(settings, "SYNC_INVOKER_SA_EMAIL", "sched@proj.iam.gserviceaccount.com")
    monkeypatch.setattr(auth, "_verify_iap_assertion",
                        lambda a: {"email": "sched@proj.iam.gserviceaccount.com"})
    monkeypatch.setattr(auth, "is_super_admin", lambda e: False)
    assert auth.require_sync_caller(_request({"x-goog-iap-jwt-assertion": "t"})) == \
        "sched@proj.iam.gserviceaccount.com"


def test_require_sync_caller_rejects_random_user(monkeypatch):
    monkeypatch.setattr(settings, "IAP_AUDIENCE", "aud")
    monkeypatch.setattr(settings, "SYNC_INVOKER_SA_EMAIL", "sched@proj.iam.gserviceaccount.com")
    monkeypatch.setattr(auth, "_verify_iap_assertion", lambda a: {"email": "rando@example.com"})
    monkeypatch.setattr(auth, "is_super_admin", lambda e: False)
    with pytest.raises(HTTPException) as ei:
        auth.require_sync_caller(_request({"x-goog-iap-jwt-assertion": "t"}))
    assert ei.value.status_code == 403
