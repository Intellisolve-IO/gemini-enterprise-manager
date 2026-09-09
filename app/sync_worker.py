import logging
import time
from datetime import datetime, timezone
from typing import Dict, Any, List, Set

from app.config import settings
from app.firestore_db import get_config, record_sync_history
from app.workspace_client import WorkspaceClient

logger = logging.getLogger("gemini_provisioner.sync")


def run_license_sync(triggered_by: str = "scheduled") -> Dict[str, Any]:
    """Core synchronization engine.
    
    1. Fetches monitored groups & SKU configuration from Firestore.
    2. Queries direct group members (flat query, no nesting).
    3. Flags and logs any nested groups encountered as explicit errors.
    4. Deduplicates individual user email addresses.
    5. Queries Google Workspace Licensing API and assigns SKU if not already held.
    6. Persists complete audit stats to Cloud Logging and Firestore sync_history.
    """
    start_time = datetime.now(timezone.utc)
    start_perf = time.perf_counter()
    logger.info("Starting Gemini Enterprise license sync (triggered by: %s)", triggered_by)

    config = get_config()
    monitored_groups: List[str] = config.get("monitored_groups", [])
    product_id: str = config.get("product_id", settings.PRODUCT_ID)
    sku_id: str = config.get("sku_id", settings.SKU_ID)
    delegated_email: str = config.get("delegated_admin_email", settings.DELEGATED_ADMIN_EMAIL)

    client = WorkspaceClient(delegated_admin_email=delegated_email)

    errors: List[Dict[str, Any]] = []
    nested_groups_skipped: List[Dict[str, str]] = []
    unique_user_emails: Set[str] = set()

    # Step 1: Process monitored groups
    if not monitored_groups:
        logger.warning("No Google Groups configured for monitoring in Firestore.")
        run_record = {
            "started_at": start_time.isoformat(),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": round(time.perf_counter() - start_perf, 2),
            "triggered_by": triggered_by,
            "status": "SUCCESS",
            "monitored_groups_count": 0,
            "monitored_groups": [],
            "evaluated_users_count": 0,
            "licenses_assigned_count": 0,
            "licenses_already_held_count": 0,
            "nested_groups_count": 0,
            "errors_count": 0,
            "errors": [],
            "message": "No groups configured for monitoring."
        }
        record_sync_history(run_record)
        return run_record

    for group_email in monitored_groups:
        logger.info("Inspecting members for group: %s", group_email)
        try:
            members = client.list_direct_group_members(group_email)
            for m in members:
                m_type = m.get("type", "").upper()
                m_email = (m.get("email") or "").strip().lower()

                if not m_email:
                    continue

                if m_type == "GROUP":
                    # Explicit failure handling for nested groups
                    err_msg = (
                        f"Group '{group_email}' contains nested group '{m_email}'. "
                        f"Nested groups are not supported for licensing. Only direct user accounts can be provisioned."
                    )
                    logger.warning(err_msg)
                    nested_entry = {
                        "parent_group": group_email,
                        "nested_group_email": m_email,
                        "error": err_msg
                    }
                    nested_groups_skipped.append(nested_entry)
                    errors.append({
                        "item": m_email,
                        "type": "NESTED_GROUP_UNSUPPORTED",
                        "error": err_msg
                    })
                elif m_type == "USER":
                    unique_user_emails.add(m_email)
                else:
                    logger.info("Ignoring non-user member type '%s': %s", m_type, m_email)

        except Exception as e:
            err_msg = f"Failed to retrieve members for group '{group_email}': {str(e)}"
            logger.error(err_msg)
            errors.append({
                "item": group_email,
                "type": "GROUP_QUERY_ERROR",
                "error": err_msg
            })

    # Step 2: Check & Assign Licenses for Deduplicated Users
    assigned_count = 0
    already_held_count = 0

    logger.info("Evaluating %d unique user(s) for SKU %s/%s", len(unique_user_emails), product_id, sku_id)

    for user_email in sorted(unique_user_emails):
        try:
            has_license = client.check_license(product_id, sku_id, user_email)
            if has_license:
                already_held_count += 1
                logger.debug("User %s already has license %s/%s", user_email, product_id, sku_id)
            else:
                logger.info("User %s lacks license. Provisioning %s/%s...", user_email, product_id, sku_id)
                success, err_desc = client.assign_license(product_id, sku_id, user_email)
                if success:
                    assigned_count += 1
                else:
                    errors.append({
                        "item": user_email,
                        "type": "LICENSE_ASSIGN_FAILED",
                        "error": err_desc
                    })
        except Exception as e:
            err_msg = f"Error evaluating license for user '{user_email}': {str(e)}"
            logger.error(err_msg)
            errors.append({
                "item": user_email,
                "type": "LICENSE_CHECK_ERROR",
                "error": err_msg
            })

    duration_sec = round(time.perf_counter() - start_perf, 2)
    end_time = datetime.now(timezone.utc)

    # Determine status
    if not errors:
        status = "SUCCESS"
    elif assigned_count > 0 or already_held_count > 0:
        status = "PARTIAL_SUCCESS"
    else:
        status = "FAILED"

    run_record = {
        "started_at": start_time.isoformat(),
        "completed_at": end_time.isoformat(),
        "duration_seconds": duration_sec,
        "triggered_by": triggered_by,
        "status": status,
        "product_id": product_id,
        "sku_id": sku_id,
        "monitored_groups_count": len(monitored_groups),
        "monitored_groups": monitored_groups,
        "evaluated_users_count": len(unique_user_emails),
        "licenses_assigned_count": assigned_count,
        "licenses_already_held_count": already_held_count,
        "nested_groups_count": len(nested_groups_skipped),
        "errors_count": len(errors),
        "errors": errors[:50],  # cap to top 50 in summary to keep document size bounded
    }

    try:
        doc_id = record_sync_history(run_record)
        run_record["doc_id"] = doc_id
    except Exception as e:
        logger.error("Could not write sync record to Firestore: %s", e)

    logger.info(
        "Sync completed in %.2fs. Evaluated: %d, Assigned: %d, Already Held: %d, Nested Groups Skipped: %d, Errors: %d",
        duration_sec,
        len(unique_user_emails),
        assigned_count,
        already_held_count,
        len(nested_groups_skipped),
        len(errors)
    )

    return run_record
