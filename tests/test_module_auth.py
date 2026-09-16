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


def test_page_dependency_raises_module_disabled_when_off():
    with patch("app.core.module_auth.is_module_enabled", return_value=False):
        dep = require_module_enabled_page("health-check")
        with pytest.raises(ModuleDisabledError) as ei:
            dep(request=None, principal=None)
        assert ei.value.module_id == "health-check"


def test_page_dependency_passes_through_when_enabled():
    with patch("app.core.module_auth.is_module_enabled", return_value=True):
        dep = require_module_enabled_page("health-check")
        assert dep(request=None, principal="admin@example.com") == "admin@example.com"


def test_api_dependency_raises_403_when_disabled():
    with patch("app.core.module_auth.is_module_enabled", return_value=False):
        dep = require_module_enabled_api("health-check")
        with pytest.raises(HTTPException) as ei:
            dep(request=None, principal="admin@example.com")
        assert ei.value.status_code == 403


def test_api_dependency_passes_through_when_enabled():
    with patch("app.core.module_auth.is_module_enabled", return_value=True):
        dep = require_module_enabled_api("health-check")
        assert dep(request=None, principal="admin@example.com") == "admin@example.com"


# -- End-to-end: a disabled module's page route redirects, an enabled one serves. --
# Uses a throwaway app wired the same way app/main.py wires ModuleDisabledError,
# rather than depending on any specific future module's real routes existing yet.

def _tiny_app():
    tiny = FastAPI()

    @tiny.exception_handler(ModuleDisabledError)
    async def _handler(request, exc):
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=f"/?disabled_module={exc.module_id}", status_code=303)

    @tiny.get("/gated")
    async def gated(principal=Depends(require_module_enabled_page("health-check"))):
        return {"ok": True}

    return tiny


def test_disabled_module_page_redirects_to_landing():
    with patch("app.core.module_auth.is_module_enabled", return_value=False), \
         patch("app.auth.current_principal", return_value=None):
        client = TestClient(_tiny_app(), follow_redirects=False)
        r = client.get("/gated")
        assert r.status_code == 303
        assert r.headers["location"] == "/?disabled_module=health-check"


def test_enabled_module_page_serves_normally():
    with patch("app.core.module_auth.is_module_enabled", return_value=True), \
         patch("app.auth.current_principal", return_value=None):
        client = TestClient(_tiny_app())
        r = client.get("/gated")
        assert r.status_code == 200
        assert r.json() == {"ok": True}
