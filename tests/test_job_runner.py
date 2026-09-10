import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import job_runner


def test_exit_zero_on_success():
    with patch("app.job_runner.run_license_sync", return_value={"status": "SUCCESS"}) as m:
        assert job_runner.main() == 0
        m.assert_called_once_with(triggered_by="scheduled")


def test_exit_zero_on_partial_success():
    with patch("app.job_runner.run_license_sync", return_value={"status": "PARTIAL_SUCCESS", "errors_count": 2}):
        assert job_runner.main() == 0


def test_exit_one_on_failed_status():
    with patch("app.job_runner.run_license_sync", return_value={"status": "FAILED", "errors_count": 1}):
        assert job_runner.main() == 1


def test_exit_one_on_unhandled_exception():
    with patch("app.job_runner.run_license_sync", side_effect=RuntimeError("boom")):
        assert job_runner.main() == 1
