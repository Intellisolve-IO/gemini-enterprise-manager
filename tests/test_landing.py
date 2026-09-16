import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_landing_lists_all_modules():
    response = client.get("/")
    assert response.status_code == 200
    for title in ("License Sync", "App URL Mapping", "Agent Deployment", "Health Check"):
        assert title in response.text


def test_toggle_module_enables_and_disables():
    with patch("app.landing.update_module_config") as mock_update:
        mock_update.return_value = {"enabled": True}
        response = client.post("/api/modules/health-check/toggle", json={"enabled": True})
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["module_id"] == "health-check"
        assert data["enabled"] is True
        mock_update.assert_called_once()
        args, kwargs = mock_update.call_args
        assert args[0] == "health-check"
        assert args[1] == {"enabled": True}


def test_toggle_unknown_module_404():
    response = client.post("/api/modules/does-not-exist/toggle", json={"enabled": True})
    assert response.status_code == 404


def test_toggle_requires_super_admin(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "IAP_AUDIENCE", "test-aud")
    response = client.post("/api/modules/health-check/toggle", json={"enabled": True})
    assert response.status_code == 401
