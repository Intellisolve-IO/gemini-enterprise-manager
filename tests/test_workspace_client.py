import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.config import settings
from app.workspace_client import WorkspaceClient, SCOPES


def test_get_credentials_delegates_to_tenant_credentials_with_sa_email(monkeypatch):
    monkeypatch.setattr(settings, "SERVICE_ACCOUNT_KEY_JSON", None)
    monkeypatch.setattr(settings, "SERVICE_ACCOUNT_KEY_PATH", None)
    client = WorkspaceClient(delegated_admin_email="admin@example.com", sa_email="tenant-sa@proj.iam.gserviceaccount.com")
    with patch("app.workspace_client.tenant_credentials.build_credentials", return_value="creds") as mock_build:
        result = client.get_credentials()
    assert result == "creds"
    mock_build.assert_called_once_with("tenant-sa@proj.iam.gserviceaccount.com", SCOPES, subject="admin@example.com")


def test_get_credentials_uses_explicit_subject_email_override(monkeypatch):
    monkeypatch.setattr(settings, "SERVICE_ACCOUNT_KEY_JSON", None)
    monkeypatch.setattr(settings, "SERVICE_ACCOUNT_KEY_PATH", None)
    client = WorkspaceClient(delegated_admin_email="admin@example.com", sa_email=None)
    with patch("app.workspace_client.tenant_credentials.build_credentials", return_value="creds") as mock_build:
        client.get_credentials(subject_email="other@example.com")
    mock_build.assert_called_once_with(None, SCOPES, subject="other@example.com")


def test_get_credentials_respects_service_account_key_json(monkeypatch):
    monkeypatch.setattr(settings, "SERVICE_ACCOUNT_KEY_JSON", '{"type": "service_account"}')
    fake_creds = MagicMock()
    fake_creds.with_subject.return_value = "subject-creds"
    with patch("app.workspace_client.service_account.Credentials.from_service_account_info",
              return_value=fake_creds), \
         patch("app.workspace_client.tenant_credentials.build_credentials") as mock_build:
        client = WorkspaceClient(delegated_admin_email="admin@example.com")
        result = client.get_credentials()
    assert result == "subject-creds"
    mock_build.assert_not_called()


def test_get_gmail_service_uses_gmail_scope():
    client = WorkspaceClient(sa_email="tenant-sa@proj.iam.gserviceaccount.com")
    with patch.object(client, "get_credentials", return_value="creds") as mock_get_creds, \
         patch("app.workspace_client.build") as mock_build:
        client.get_gmail_service(subject_email="admin@example.com")
    mock_get_creds.assert_called_once()
    args, kwargs = mock_get_creds.call_args
    assert kwargs.get("scopes") == ["https://www.googleapis.com/auth/gmail.send"]
    mock_build.assert_called_once_with("gmail", "v1", credentials="creds", cache_discovery=False)
