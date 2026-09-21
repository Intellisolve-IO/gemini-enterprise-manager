import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core.module_auth import (
    ModuleDisabledError,
    require_module_enabled_api,
    require_module_enabled_page,
)
from app.core.session_auth import SESSION_COOKIE_NAME

_USER = {"uid": "u1", "email": "admin@example.com"}
_MEMBER = {"uid": "u1", "role": "owner", "email": "admin@example.com"}
_ENVIRONMENT = {"id": "env1", "tenant_id": "t1", "display_name": "Prod"}


def _tiny_app():
    tiny = FastAPI()

    @tiny.exception_handler(ModuleDisabledError)
    async def _handler(request, exc):
        from fastapi.responses import RedirectResponse
        url = f"/t/{exc.tenant_id}/e/{exc.environment_id}/?disabled_module={exc.module_id}"
        return RedirectResponse(url=url, status_code=303)

    @tiny.get("/t/{tenant_id}/e/{environment_id}/gated")
    async def gated_page(_=Depends(require_module_enabled_page("health-check"))):
        return {"ok": True}

    @tiny.post("/t/{tenant_id}/e/{environment_id}/gated-api")
    async def gated_api(_=Depends(require_module_enabled_api("health-check"))):
        return {"ok": True}

    return tiny


def _client():
    client = TestClient(_tiny_app(), follow_redirects=False)
    client.cookies.set(SESSION_COOKIE_NAME, "fake-cookie")
    return client


def _auth_chain_mocks(member=_MEMBER, environment=_ENVIRONMENT):
    return (
        patch("app.core.session_auth.verify_session_cookie", return_value=_USER),
        patch("app.core.tenants.get_member", return_value=member),
        patch("app.core.tenants.get_environment", return_value=environment),
    )


def test_disabled_module_page_redirects_to_landing():
    m1, m2, m3 = _auth_chain_mocks()
    with m1, m2, m3, patch("app.core.module_auth.is_module_enabled", return_value=False):
        r = _client().get("/t/t1/e/env1/gated")
    assert r.status_code == 303
    assert r.headers["location"] == "/t/t1/e/env1/?disabled_module=health-check"


def test_enabled_module_page_serves_normally():
    m1, m2, m3 = _auth_chain_mocks()
    with m1, m2, m3, patch("app.core.module_auth.is_module_enabled", return_value=True):
        r = _client().get("/t/t1/e/env1/gated")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_disabled_module_api_returns_403():
    m1, m2, m3 = _auth_chain_mocks()
    with m1, m2, m3, patch("app.core.module_auth.is_module_enabled", return_value=False):
        r = _client().post("/t/t1/e/env1/gated-api")
    assert r.status_code == 403


def test_enabled_module_api_serves_normally():
    m1, m2, m3 = _auth_chain_mocks()
    with m1, m2, m3, patch("app.core.module_auth.is_module_enabled", return_value=True):
        r = _client().post("/t/t1/e/env1/gated-api")
    assert r.status_code == 200


def test_non_member_gets_403_before_module_check():
    """A non-member never learns whether the module is enabled - auth/membership
    is checked first."""
    m1, m2, m3 = _auth_chain_mocks(member=None)
    with m1, m2, m3, patch("app.core.module_auth.is_module_enabled") as mock_enabled:
        r = _client().get("/t/t1/e/env1/gated")
    assert r.status_code == 403
    mock_enabled.assert_not_called()


def test_unauthenticated_gets_401():
    with patch("app.core.session_auth.verify_session_cookie", side_effect=Exception("bad token")):
        r = _client().get("/t/t1/e/env1/gated")
    assert r.status_code == 401


def test_unknown_environment_gets_404():
    m1, m2, m3 = _auth_chain_mocks(environment=None)
    with m1, m2, m3:
        r = _client().get("/t/t1/e/env1/gated")
    assert r.status_code == 404
