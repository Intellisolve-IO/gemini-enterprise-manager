import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_healthz():
    """Verify healthcheck probe returns 200."""
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_dashboard_route():
    """Verify dashboard renders HTML."""
    mock_config = {
        "monitored_groups": ["team@example.com"],
        "product_id": "Google-Apps",
        "sku_id": "101031",
        "delegated_admin_email": "admin@example.com",
        "cron_expression": "0 2 * * *"
    }
    with patch("app.main.get_config", return_value=mock_config), \
         patch("app.main.get_sync_history", return_value=[]):
        response = client.get("/")
        assert response.status_code == 200
        assert "Gemini License Provisioning Dashboard" in response.text
        assert "team@example.com" in response.text


def test_api_save_groups():
    """Verify saving monitored groups via API."""
    with patch("app.main.update_config", return_value={"monitored_groups": ["group1@domain.com"]}):
        response = client.post(
            "/api/groups",
            json={"groups": ["group1@domain.com"]}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["monitored_groups"] == ["group1@domain.com"]


def test_api_save_settings():
    """Verify saving settings via API."""
    with patch("app.main.update_config", return_value={}):
        response = client.post(
            "/api/settings",
            json={
                "delegated_admin_email": "admin@test.com",
                "product_id": "101047",
                "sku_id": "1010470001"
            }
        )
        assert response.status_code == 200
        assert response.json()["success"] is True


def test_api_test_connection():
    """Verify DWD test connection endpoint returns result structure."""
    mock_res = {
        "success": True,
        "message": "Connected",
        "subject": "admin@test.com"
    }
    with patch("app.main.WorkspaceClient") as MockClient:
        mock_instance = MagicMock()
        mock_instance.test_dwd_connection.return_value = mock_res
        MockClient.return_value = mock_instance

        response = client.post(
            "/api/test-connection",
            json={"delegated_admin_email": "admin@test.com"}
        )
        assert response.status_code == 200
        assert response.json()["success"] is True
