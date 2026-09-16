import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import job_runner
from app.config import settings


def _configured(monkeypatch):
    monkeypatch.setattr(settings, "SCHEDULED_SYNC_TENANT_ID", "tenant1")
    monkeypatch.setattr(settings, "SCHEDULED_SYNC_ENVIRONMENT_ID", "env1")


def test_exit_one_when_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "SCHEDULED_SYNC_TENANT_ID", "")
    monkeypatch.setattr(settings, "SCHEDULED_SYNC_ENVIRONMENT_ID", "")
    with patch("app.job_runner.run_license_sync") as m:
        assert job_runner.main() == 1
        m.assert_not_called()


def test_exit_zero_on_success(monkeypatch):
    _configured(monkeypatch)
    with patch("app.job_runner.run_license_sync", return_value={"status": "SUCCESS"}) as m:
        assert job_runner.main() == 0
        m.assert_called_once_with("tenant1", "env1", triggered_by="scheduled")


def test_exit_zero_on_partial_success(monkeypatch):
    _configured(monkeypatch)
    with patch("app.job_runner.run_license_sync", return_value={"status": "PARTIAL_SUCCESS", "errors_count": 2}):
        assert job_runner.main() == 0


def test_exit_one_on_failed_status(monkeypatch):
    _configured(monkeypatch)
    with patch("app.job_runner.run_license_sync", return_value={"status": "FAILED", "errors_count": 1}):
        assert job_runner.main() == 1


def test_exit_one_on_unhandled_exception(monkeypatch):
    _configured(monkeypatch)
    with patch("app.job_runner.run_license_sync", side_effect=RuntimeError("boom")):
        assert job_runner.main() == 1
