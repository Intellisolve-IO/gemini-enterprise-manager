import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import notifications


BASE_RUN = {
    "status": "PARTIAL_SUCCESS",
    "triggered_by": "scheduled",
    "started_at": "2026-09-10T02:00:00+00:00",
    "completed_at": "2026-09-10T02:00:07+00:00",
    "duration_seconds": 7.0,
    "monitored_groups": ["eng@example.com"],
    "monitored_groups_count": 1,
    "evaluated_users_count": 3,
    "licenses_assigned_count": 1,
    "licenses_already_held_count": 1,
    "nested_groups_count": 1,
    "errors_count": 1,
    "errors": [{"item": "sub@example.com", "type": "NESTED_GROUP_UNSUPPORTED", "error": "nested"}],
    "doc_id": "run123",
}


def test_should_notify_respects_mode_and_recipients():
    cfg_none = {"notification_emails": [], "notify_on": "all"}
    assert notifications.should_notify(cfg_none, BASE_RUN) is False

    cfg_fail = {"notification_emails": ["a@example.com"], "notify_on": "failures"}
    assert notifications.should_notify(cfg_fail, {**BASE_RUN, "status": "SUCCESS"}) is False
    assert notifications.should_notify(cfg_fail, {**BASE_RUN, "status": "FAILED"}) is True
    assert notifications.should_notify(cfg_fail, {**BASE_RUN, "status": "PARTIAL_SUCCESS"}) is True

    cfg_all = {"notification_emails": ["a@example.com"], "notify_on": "all"}
    assert notifications.should_notify(cfg_all, {**BASE_RUN, "status": "SUCCESS"}) is True


def test_build_message_contains_full_detail_and_history_link():
    cfg = {"notification_emails": ["a@example.com"], "notify_on": "all",
           "public_base_url": "https://prov.example.com"}
    msg = notifications.build_message(cfg, BASE_RUN, "tenant1", "env1")
    assert "PARTIAL_SUCCESS" in msg["subject"]
    for token in ("eng@example.com", "Cloud Scheduler cron trigger", "Licenses assigned",
                  "sub@example.com", "run123"):
        assert token in msg["text"]
    expected_url = "https://prov.example.com/t/tenant1/e/env1/modules/license-sync/history"
    assert expected_url in msg["text"]
    assert expected_url in msg["html"]


def test_send_sync_notification_uses_gmail_and_never_raises():
    cfg = {"notification_emails": ["ops@example.com"], "notify_on": "all",
           "delegated_admin_email": "admin@example.com"}
    with patch("app.notifications.WorkspaceClient") as MockClient:
        gmail = MagicMock()
        MockClient.return_value.get_gmail_service.return_value = gmail
        result = notifications.send_sync_notification(cfg, BASE_RUN, "tenant1", "env1")
        assert result["sent"] is True
        gmail.users.return_value.messages.return_value.send.assert_called_once()

    with patch("app.notifications.WorkspaceClient", side_effect=RuntimeError("boom")):
        result = notifications.send_sync_notification(cfg, BASE_RUN, "tenant1", "env1")
        assert result["sent"] is False
        assert "boom" in result["reason"]


def test_send_skipped_when_not_required():
    cfg = {"notification_emails": [], "notify_on": "all"}
    result = notifications.send_sync_notification(cfg, BASE_RUN, "tenant1", "env1")
    assert result["sent"] is False
