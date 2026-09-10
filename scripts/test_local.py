#!/usr/bin/env python3
"""Local verification script for Gemini Enterprise License Provisioner.

Runs tests on the core synchronization logic, verifying:
1. Direct members of Google Groups are processed.
2. Nested groups (type == 'GROUP') are flagged and logged as unsupported.
3. Users lacking license are provisioned.
4. Users already holding license are skipped.
5. Deduplication across multiple groups works properly.
"""

import sys
import os

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from unittest.mock import MagicMock

# Mock third-party dependencies if running in an environment where pip dependencies aren't yet installed
for mod in [
    "pydantic", "pydantic_settings", "google", "google.cloud", "google.cloud.firestore",
    "google.cloud.scheduler_v1", "google.oauth2", "google.oauth2.service_account",
    "google.auth", "google.auth.iam", "google.auth.transport", "google.auth.transport.requests",
    "googleapiclient", "googleapiclient.discovery", "googleapiclient.errors",
    "fastapi", "fastapi.staticfiles", "fastapi.templating", "jinja2"
]:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

# Configure BaseSettings mock
class MockBaseSettings:
    def __init__(self, **kwargs):
        for k, v in self.__class__.__dict__.items():
            # Skip dunders and descriptors (e.g. @property) - they stay on the class.
            if not k.startswith("_") and not isinstance(v, (property, staticmethod, classmethod)):
                setattr(self, k, v)

sys.modules["pydantic_settings"].BaseSettings = MockBaseSettings
sys.modules["pydantic_settings"].SettingsConfigDict = lambda **kwargs: None

from app.sync_worker import run_license_sync
from unittest.mock import patch


def run_tests():
    print("===================================================================")
    print(" Running Local Tests: Gemini License Provisioner Sync Engine")
    print("===================================================================")

    # Test Case 1: Nested groups skipped & flagged, direct users licensed
    print("\n[Test 1] Testing Flat Group Membership & Nested Group Error Logging...")
    mock_config = {
        "monitored_groups": ["ai-engineers@example.com"],
        "product_id": "Google-Apps",
        "sku_id": "101031",
        "delegated_admin_email": "admin@example.com"
    }

    # Group has:
    # 1. user1: needs license
    # 2. user2: already has license
    # 3. subgroup: nested group (MUST be rejected with error log)
    mock_members = [
        {"email": "user1@example.com", "type": "USER"},
        {"email": "user2@example.com", "type": "USER"},
        {"email": "subgroup@example.com", "type": "GROUP"},
    ]

    with patch("app.sync_worker.get_config", return_value=mock_config), \
         patch("app.sync_worker.record_sync_history", return_value="mock_doc_id"), \
         patch("app.sync_worker.WorkspaceClient") as MockClientClass:

        mock_client = MagicMock()
        MockClientClass.return_value = mock_client
        mock_client.list_direct_group_members.return_value = mock_members

        # user1 has no license, user2 already has license
        def mock_check(prod, sku, user):
            return user == "user2@example.com"
        
        mock_client.check_license.side_effect = mock_check
        mock_client.assign_license.return_value = (True, None)

        result = run_license_sync(triggered_by="local_test")

        # Verify results
        assert result["status"] == "PARTIAL_SUCCESS", f"Expected PARTIAL_SUCCESS, got {result['status']}"
        assert result["evaluated_users_count"] == 2, f"Expected 2 evaluated users, got {result['evaluated_users_count']}"
        assert result["licenses_assigned_count"] == 1, f"Expected 1 license assigned, got {result['licenses_assigned_count']}"
        assert result["licenses_already_held_count"] == 1, f"Expected 1 license already held, got {result['licenses_already_held_count']}"
        assert result["nested_groups_count"] == 1, f"Expected 1 nested group, got {result['nested_groups_count']}"

        # Verify error structure
        assert result["errors_count"] == 1
        assert "subgroup@example.com" in result["errors"][0]["item"]
        assert "Nested groups are not supported" in result["errors"][0]["error"]

        # Verify assign_license was called only for user1
        mock_client.assign_license.assert_called_once_with("Google-Apps", "101031", "user1@example.com")

        print("  ✓ Evaluated users correctly: 2")
        print("  ✓ Nested group 'subgroup@example.com' caught and logged as error")
        print("  ✓ License successfully assigned to 'user1@example.com'")
        print("  ✓ License skipped for 'user2@example.com' (already held)")
        print("  ✓ Test 1 PASSED!")

    # Test Case 2: Multi-group user deduplication
    print("\n[Test 2] Testing Multi-Group Member Deduplication...")
    mock_config = {
        "monitored_groups": ["group-a@example.com", "group-b@example.com"],
        "product_id": "Google-Apps",
        "sku_id": "101031",
        "delegated_admin_email": "admin@example.com"
    }

    # charlie is in both group-a and group-b
    group_a_members = [
        {"email": "charlie@example.com", "type": "USER"},
        {"email": "dan@example.com", "type": "USER"}
    ]
    group_b_members = [
        {"email": "charlie@example.com", "type": "USER"},
        {"email": "eve@example.com", "type": "USER"}
    ]

    with patch("app.sync_worker.get_config", return_value=mock_config), \
         patch("app.sync_worker.record_sync_history", return_value="mock_doc_id"), \
         patch("app.sync_worker.WorkspaceClient") as MockClientClass:

        mock_client = MagicMock()
        MockClientClass.return_value = mock_client
        mock_client.list_direct_group_members.side_effect = lambda g: group_a_members if "group-a" in g else group_b_members
        mock_client.check_license.return_value = False
        mock_client.assign_license.return_value = (True, None)

        result = run_license_sync(triggered_by="local_test")

        # charlie, dan, eve => total 3 unique users (not 4)
        assert result["evaluated_users_count"] == 3, f"Expected 3 unique users, got {result['evaluated_users_count']}"
        assert result["licenses_assigned_count"] == 3, f"Expected 3 licenses assigned, got {result['licenses_assigned_count']}"
        assert result["status"] == "SUCCESS"
        assert result["errors_count"] == 0

        print("  ✓ Total groups processed: 2")
        print("  ✓ Duplicate user 'charlie@example.com' deduplicated across groups")
        print("  ✓ Exactly 3 licenses assigned")
        print("  ✓ Test 2 PASSED!")

    # Test Case 3: Empty groups configuration
    print("\n[Test 3] Testing Empty Monitored Groups...")
    mock_config = {
        "monitored_groups": [],
        "product_id": "Google-Apps",
        "sku_id": "101031",
        "delegated_admin_email": "admin@example.com"
    }

    with patch("app.sync_worker.get_config", return_value=mock_config), \
         patch("app.sync_worker.record_sync_history", return_value="mock_doc_id"):

        result = run_license_sync(triggered_by="local_test")
        assert result["status"] == "SUCCESS"
        assert result["evaluated_users_count"] == 0
        assert result["licenses_assigned_count"] == 0
        print("  ✓ Gracefully handled 0 groups")
        print("  ✓ Test 3 PASSED!")

    print("\n===================================================================")
    print(" ALL LOCAL UNIT TESTS PASSED SUCCESSFULLY! (3/3)")
    print("===================================================================")


if __name__ == "__main__":
    run_tests()
