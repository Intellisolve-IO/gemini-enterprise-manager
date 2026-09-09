import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.sync_worker import run_license_sync


def test_sync_engine_handles_nested_group_failure():
    """Test that nested groups are logged as explicit errors and not recursed/assigned licenses."""
    mock_config = {
        "monitored_groups": ["ai-team@hoffhouse.com"],
        "product_id": "Google-Apps",
        "sku_id": "101031",
        "delegated_admin_email": "admin@hoffhouse.com"
    }

    # Mock direct members of ai-team@hoffhouse.com:
    # 1 valid user needing license
    # 1 valid user already licensed
    # 1 nested group (which must be flagged as unsupported and skipped)
    mock_members = [
        {"email": "alice@hoffhouse.com", "type": "USER"},
        {"email": "bob@hoffhouse.com", "type": "USER"},
        {"email": "subteam-nested@hoffhouse.com", "type": "GROUP"},
    ]

    with patch("app.sync_worker.get_config", return_value=mock_config), \
         patch("app.sync_worker.record_sync_history", return_value="mock_doc_id"), \
         patch("app.sync_worker.WorkspaceClient") as MockClientClass:
        
        mock_client = MagicMock()
        MockClientClass.return_value = mock_client
        mock_client.list_direct_group_members.return_value = mock_members

        # Alice lacks license (False), Bob already has license (True)
        def mock_check_license(prod, sku, user):
            if user == "alice@hoffhouse.com":
                return False
            return True
        mock_client.check_license.side_effect = mock_check_license
        mock_client.assign_license.return_value = (True, None)

        # Run sync
        result = run_license_sync(triggered_by="test")

        # Assertions
        assert result["status"] == "PARTIAL_SUCCESS"  # because there was 1 nested group error
        assert result["evaluated_users_count"] == 2   # Alice and Bob
        assert result["licenses_assigned_count"] == 1  # Alice
        assert result["licenses_already_held_count"] == 1  # Bob
        assert result["nested_groups_count"] == 1     # subteam-nested@hoffhouse.com

        # Verify nested group error was logged in errors array
        nested_errors = [e for e in result["errors"] if e["type"] == "NESTED_GROUP_UNSUPPORTED"]
        assert len(nested_errors) == 1
        assert "subteam-nested@hoffhouse.com" in nested_errors[0]["item"]
        assert "Nested groups are not supported" in nested_errors[0]["error"]

        # Verify assign_license was called ONLY for Alice, never for the nested group
        mock_client.assign_license.assert_called_once_with("Google-Apps", "101031", "alice@hoffhouse.com")


def test_sync_engine_empty_groups():
    """Test sync engine behavior when no monitored groups are configured."""
    mock_config = {
        "monitored_groups": [],
        "product_id": "Google-Apps",
        "sku_id": "101031",
        "delegated_admin_email": "admin@hoffhouse.com"
    }

    with patch("app.sync_worker.get_config", return_value=mock_config), \
         patch("app.sync_worker.record_sync_history", return_value="mock_doc_id"):
        
        result = run_license_sync(triggered_by="scheduled")
        assert result["status"] == "SUCCESS"
        assert result["evaluated_users_count"] == 0
        assert result["licenses_assigned_count"] == 0
        assert "No groups configured" in result["message"]
