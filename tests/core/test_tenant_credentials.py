import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest

from app.config import settings
from app.core import tenant_credentials


def test_build_credentials_impersonates_explicit_target(monkeypatch):
    source = MagicMock()
    with patch("app.core.tenant_credentials._source_credentials", return_value=source), \
         patch("app.core.tenant_credentials.impersonated_credentials.Credentials") as MockCreds:
        MockCreds.return_value = "impersonated"
        result = tenant_credentials.build_credentials("tenant-sa@proj.iam.gserviceaccount.com", ["scope1"])
    assert result == "impersonated"
    _, kwargs = MockCreds.call_args
    assert kwargs["source_credentials"] is source
    assert kwargs["target_principal"] == "tenant-sa@proj.iam.gserviceaccount.com"
    assert kwargs["target_scopes"] == ["scope1"]
    assert kwargs["subject"] is None


def test_build_credentials_passes_subject_for_dwd():
    with patch("app.core.tenant_credentials._source_credentials", return_value=MagicMock()), \
         patch("app.core.tenant_credentials.impersonated_credentials.Credentials") as MockCreds:
        tenant_credentials.build_credentials("sa@proj.iam.gserviceaccount.com", ["scope1"],
                                              subject="admin@example.com")
    _, kwargs = MockCreds.call_args
    assert kwargs["subject"] == "admin@example.com"


def test_build_credentials_falls_back_to_central_runtime_sa(monkeypatch):
    monkeypatch.setattr(settings, "RUNTIME_SERVICE_ACCOUNT_EMAIL", "central-sa@proj.iam.gserviceaccount.com")
    with patch("app.core.tenant_credentials._source_credentials", return_value=MagicMock()), \
         patch("app.core.tenant_credentials.impersonated_credentials.Credentials") as MockCreds:
        tenant_credentials.build_credentials(None, ["scope1"])
    _, kwargs = MockCreds.call_args
    assert kwargs["target_principal"] == "central-sa@proj.iam.gserviceaccount.com"


def test_build_credentials_returns_source_when_no_target_and_no_subject(monkeypatch):
    monkeypatch.setattr(settings, "RUNTIME_SERVICE_ACCOUNT_EMAIL", None)
    source = MagicMock()
    with patch("app.core.tenant_credentials._source_credentials", return_value=source):
        result = tenant_credentials.build_credentials(None, ["scope1"])
    assert result is source


def test_build_credentials_raises_clear_error_for_dwd_without_any_target(monkeypatch):
    monkeypatch.setattr(settings, "RUNTIME_SERVICE_ACCOUNT_EMAIL", None)
    with patch("app.core.tenant_credentials._source_credentials", return_value=MagicMock()):
        with pytest.raises(RuntimeError, match="Domain-Wide Delegation"):
            tenant_credentials.build_credentials(None, ["scope1"], subject="admin@example.com")


def test_test_tenant_impersonation_success():
    service = MagicMock()
    with patch("app.core.tenant_credentials.build_credentials", return_value=MagicMock()), \
         patch("googleapiclient.discovery.build", return_value=service):
        result = tenant_credentials.test_tenant_impersonation("tenant-sa@proj.iam.gserviceaccount.com", "proj")
    assert result["success"] is True
    assert result["subject"] == "tenant-sa@proj.iam.gserviceaccount.com"


def test_test_tenant_impersonation_failure_includes_guidance(monkeypatch):
    monkeypatch.setattr(settings, "RUNTIME_SERVICE_ACCOUNT_EMAIL", "central-sa@proj.iam.gserviceaccount.com")
    with patch("app.core.tenant_credentials.build_credentials", side_effect=RuntimeError("no access")):
        result = tenant_credentials.test_tenant_impersonation("tenant-sa@proj.iam.gserviceaccount.com", "proj")
    assert result["success"] is False
    assert "no access" in result["message"]
    assert "central-sa@proj.iam.gserviceaccount.com" in result["guidance"]


def test_project_id_for_uses_environment_project_when_set():
    assert tenant_credentials.project_id_for({"gcp_project_id": "tenant-proj"}) == "tenant-proj"


def test_project_id_for_falls_back_to_central_project(monkeypatch):
    monkeypatch.setattr(settings, "GCP_PROJECT_ID", "central-proj")
    assert tenant_credentials.project_id_for({"gcp_project_id": ""}) == "central-proj"
