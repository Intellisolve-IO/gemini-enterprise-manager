"""Individual Gemini Enterprise health checks. Each returns a uniform dict:
{id, label, status: "pass"|"warn"|"fail", message, guidance, checked_at}

Deliberately reuses existing clients wherever one already exists
(GeminiLicenseClient, WorkspaceClient, SchedulerService, Firestore) rather than
reimplementing connectivity checks the app already has.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.config import settings
from app.core.module_config import is_module_enabled
from app.firestore_db import get_config
from app.gemini_licensing import GeminiLicenseClient
from app.modules.health_check import iam_client
from app.scheduler_service import SchedulerService
from app.workspace_client import WorkspaceClient

logger = logging.getLogger("gemini_provisioner.health_check.checks")

# Baseline IAM roles the runtime service account is documented to need for the
# currently-enabled modules (see setup_instructions.md -> Required Privileges).
# Extra roles beyond this set are flagged WARN (over-privilege), never FAIL.
_BASELINE_SA_ROLES = {
    "roles/datastore.user",
    "roles/discoveryengine.admin",
    "roles/cloudscheduler.admin",
    "roles/logging.logWriter",
    "roles/iam.securityReviewer",
    "roles/serviceusage.serviceUsageViewer",
}

_BASELINE_SERVICES = {
    "admin.googleapis.com",
    "discoveryengine.googleapis.com",
    "cloudscheduler.googleapis.com",
    "firestore.googleapis.com",
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "iamcredentials.googleapis.com",
    "cloudbuild.googleapis.com",
    "gmail.googleapis.com",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _result(id_: str, label: str, status_: str, message: str, guidance: Optional[str] = None) -> Dict[str, Any]:
    return {"id": id_, "label": label, "status": status_, "message": message,
            "guidance": guidance, "checked_at": _now()}


def check_iam_roles() -> Dict[str, Any]:
    sa_email = settings.RUNTIME_SERVICE_ACCOUNT_EMAIL
    if not sa_email:
        return _result("iam_roles", "IAM Roles on Runtime Service Account", "fail",
                        "RUNTIME_SERVICE_ACCOUNT_EMAIL is not set.",
                        "Set the RUNTIME_SERVICE_ACCOUNT_EMAIL environment variable to the "
                        "Cloud Run service account email.")
    member = f"serviceAccount:{sa_email}"
    try:
        bindings = iam_client.get_project_iam_bindings(settings.GCP_PROJECT_ID)
    except Exception as e:
        return _result("iam_roles", "IAM Roles on Runtime Service Account", "fail",
                        f"Could not read project IAM policy: {e}",
                        "Grant roles/iam.securityReviewer to the runtime service account so "
                        "this check itself can run.")

    granted = {b["role"] for b in bindings if member in b.get("members", [])}
    missing = _BASELINE_SA_ROLES - granted
    extra = granted - _BASELINE_SA_ROLES
    if missing:
        cmds = "; ".join(
            f"gcloud projects add-iam-policy-binding {settings.GCP_PROJECT_ID} "
            f"--member=serviceAccount:{sa_email} --role={r}"
            for r in sorted(missing)
        )
        return _result("iam_roles", "IAM Roles on Runtime Service Account", "fail",
                        f"Missing {len(missing)} required role(s): {', '.join(sorted(missing))}.", cmds)
    if extra:
        return _result("iam_roles", "IAM Roles on Runtime Service Account", "warn",
                        f"Granted beyond the documented baseline: {', '.join(sorted(extra))}.",
                        "Not a functional problem - review whether these extra roles are still needed.")
    return _result("iam_roles", "IAM Roles on Runtime Service Account", "pass",
                    "All documented baseline roles are granted.")


def check_self_impersonation() -> Dict[str, Any]:
    sa_email = settings.RUNTIME_SERVICE_ACCOUNT_EMAIL
    if not sa_email:
        return _result("self_impersonation", "Self-Impersonation (Keyless DWD)", "fail",
                        "RUNTIME_SERVICE_ACCOUNT_EMAIL is not set.")
    try:
        bindings = iam_client.get_service_account_iam_bindings(sa_email)
    except Exception as e:
        return _result("self_impersonation", "Self-Impersonation (Keyless DWD)", "fail",
                        f"Could not read the service account's own IAM policy: {e}",
                        "Grant roles/iam.securityReviewer to the runtime service account.")

    member = f"serviceAccount:{sa_email}"
    has_token_creator = any(
        b.get("role") == "roles/iam.serviceAccountTokenCreator" and member in b.get("members", [])
        for b in bindings
    )
    if has_token_creator:
        return _result("self_impersonation", "Self-Impersonation (Keyless DWD)", "pass",
                        "roles/iam.serviceAccountTokenCreator is granted on itself.")
    return _result(
        "self_impersonation", "Self-Impersonation (Keyless DWD)", "fail",
        "roles/iam.serviceAccountTokenCreator is NOT granted on the service account itself.",
        "This is the most common cause of Settings -> Test Connection returning "
        "\"404: Domain not found\" (see setup_instructions.md). Run: "
        f"gcloud iam service-accounts add-iam-policy-binding {sa_email} "
        f"--member=serviceAccount:{sa_email} --role=roles/iam.serviceAccountTokenCreator",
    )


def check_apis_enabled() -> Dict[str, Any]:
    try:
        enabled = set(iam_client.list_enabled_services(settings.GCP_PROJECT_ID))
    except Exception as e:
        return _result("apis_enabled", "Required APIs Enabled", "fail",
                        f"Could not list enabled services: {e}",
                        "Grant roles/serviceusage.serviceUsageViewer to the runtime service account.")
    missing = _BASELINE_SERVICES - enabled
    if missing:
        cmd = ("gcloud services enable " + " ".join(sorted(missing))
               + f" --project={settings.GCP_PROJECT_ID}")
        return _result("apis_enabled", "Required APIs Enabled", "fail",
                        f"{len(missing)} required API(s) not enabled: {', '.join(sorted(missing))}.", cmd)
    return _result("apis_enabled", "Required APIs Enabled", "pass", "All required APIs are enabled.")


def check_license_subscription() -> Dict[str, Any]:
    try:
        configs = GeminiLicenseClient().list_license_configs()
    except Exception as e:
        return _result("license_subscription", "Gemini Enterprise License Subscription", "fail",
                        f"Could not reach the Discovery Engine licensing API: {e}",
                        "Confirm roles/discoveryengine.admin is granted and "
                        "discoveryengine.googleapis.com is enabled.")
    if not configs:
        return _result("license_subscription", "Gemini Enterprise License Subscription", "warn",
                        "No Gemini Enterprise license subscriptions found in this project.",
                        "Create one in the Gemini Enterprise console (Manage subscriptions).")
    return _result("license_subscription", "Gemini Enterprise License Subscription", "pass",
                    f"{len(configs)} subscription(s) found.")


def check_dwd_connectivity(tenant_id: str, environment_id: str) -> Dict[str, Any]:
    delegated_email = get_config(tenant_id, environment_id).get("delegated_admin_email", "")
    result = WorkspaceClient(delegated_admin_email=delegated_email).test_dwd_connection(delegated_email)
    status_ = "pass" if result.get("success") else "fail"
    return _result("dwd_connectivity", "Domain-Wide Delegation Connectivity", status_,
                    result.get("message", ""), result.get("guidance"))


def check_firestore(tenant_id: str, environment_id: str) -> Dict[str, Any]:
    try:
        get_config(tenant_id, environment_id)
    except Exception as e:
        return _result("firestore", "Firestore Reachability", "fail", f"Could not reach Firestore: {e}",
                        "Confirm roles/datastore.user is granted and a Native-mode database exists.")
    return _result("firestore", "Firestore Reachability", "pass", "Firestore is reachable.")


def check_scheduler(tenant_id: str, environment_id: str) -> Optional[Dict[str, Any]]:
    """Only meaningful if the license-sync module (the only scheduled feature
    today) is enabled for this environment. NOTE: today every environment
    shares the same single Cloud Scheduler job (the per-environment scheduling
    fan-out is Phase 5, not yet built), so this check's result is currently
    identical across environments - see license_sync/router.py's
    update_sync_schedule() for the same caveat."""
    if not is_module_enabled(tenant_id, environment_id, "license-sync"):
        return None
    try:
        status_info = SchedulerService().get_schedule()
    except Exception as e:
        return _result("scheduler", "Cloud Scheduler Reachability", "fail",
                        f"Could not reach Cloud Scheduler: {e}")
    if status_info.get("available"):
        return _result("scheduler", "Cloud Scheduler Reachability", "pass",
                        f"Scheduler job '{status_info.get('job_name')}' reachable: "
                        f"{status_info.get('schedule')} ({status_info.get('time_zone')}).")
    return _result("scheduler", "Cloud Scheduler Reachability", "warn",
                    status_info.get("error", "Scheduler job not found or not yet created."))


def run_all_checks(tenant_id: str, environment_id: str) -> Dict[str, Any]:
    """Run every check for one environment and return {checks, counts, ran_at}.

    check_iam_roles/check_self_impersonation/check_apis_enabled audit the
    *central app's own* runtime identity/project, not yet the environment's
    tenant-owned service account - that per-environment credential/IAM
    inspection is Phase 2 of the multi-tenant conversion (see
    app/sync_worker.py's docstring for the same note)."""
    checks = [
        check_iam_roles(),
        check_self_impersonation(),
        check_apis_enabled(),
        check_license_subscription(),
        check_dwd_connectivity(tenant_id, environment_id),
        check_firestore(tenant_id, environment_id),
    ]
    scheduler_check = check_scheduler(tenant_id, environment_id)
    if scheduler_check:
        checks.append(scheduler_check)

    counts = {"pass": 0, "warn": 0, "fail": 0}
    for c in checks:
        counts[c["status"]] = counts.get(c["status"], 0) + 1

    return {"checks": checks, "counts": counts, "ran_at": _now()}
