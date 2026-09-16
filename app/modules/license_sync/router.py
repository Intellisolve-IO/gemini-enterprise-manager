"""License Sync module: syncs Gemini Enterprise licenses from Google Group
membership. This is "module 1" of the admin console.

Mounted in app/main.py under the shared /t/{tenant_id}/e/{environment_id}
prefix, so every route here is environment-scoped; `tenant_id`/`environment_id`
arrive as ordinary FastAPI path parameters (bound from that prefix) on every
route function.

WorkspaceClient/GeminiLicenseClient calls below pass the environment's own
sa_email (from Depends(_page)/Depends(_api), which returns the environment
record) so they act as that environment's tenant-owned service account - or
the central app's own identity when the environment hasn't registered one
yet (sa_email empty, tenant-zero). SchedulerService still points at the one
shared Cloud Scheduler job used before the multi-tenant conversion - the
per-environment scheduling fan-out is later work.
"""
import logging
import re
from typing import Any, Dict, List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from app.firestore_db import get_config, update_config, get_sync_history
from app.workspace_client import WorkspaceClient
from app.gemini_licensing import GeminiLicenseClient, is_valid_config_name as gem_is_valid_config_name
from app.sync_worker import run_license_sync
from app.scheduler_service import SchedulerService
from app.core import tenant_credentials
from app.core.module_auth import require_module_enabled_api, require_module_enabled_page
from app.core.rendering import render

logger = logging.getLogger("gemini_provisioner.license_sync")

router = APIRouter()

_MODULE_ID = "license-sync"
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_page = require_module_enabled_page(_MODULE_ID)
_api = require_module_enabled_api(_MODULE_ID)


# -------------------------------------------------------------------------
# Request Schemas
# -------------------------------------------------------------------------
class GroupsPayload(BaseModel):
    groups: List[str]


class SchedulePayload(BaseModel):
    cron_expression: str


class NotificationsPayload(BaseModel):
    # Accept a list or a raw comma/newline separated string from the form.
    notification_emails: Union[List[str], str] = []
    notify_on: str = "failures"  # "failures" or "all"


class SettingsPayload(BaseModel):
    delegated_admin_email: str
    license_config: str = ""   # Discovery Engine license config resource name


class SyncTriggerPayload(BaseModel):
    triggered_by: Optional[str] = "admin_ui"


class DwdTestPayload(BaseModel):
    delegated_admin_email: Optional[str] = None


# -------------------------------------------------------------------------
# HTML Views
# -------------------------------------------------------------------------
@router.get("/modules/license-sync", response_class=HTMLResponse)
async def dashboard_view(request: Request, tenant_id: str, environment_id: str,
                          _: Dict[str, Any] = Depends(_page)):
    """License Sync module dashboard."""
    config = get_config(tenant_id, environment_id)
    history = get_sync_history(tenant_id, environment_id, limit=1)
    last_run = history[0] if history else None

    return render(request, "license_sync/dashboard.html", {
        "active_page": "license-sync",
        "license_sync_page": "dashboard",
        "config": config,
        "last_run": last_run,
    })


@router.get("/modules/license-sync/groups", response_class=HTMLResponse)
async def groups_view(request: Request, tenant_id: str, environment_id: str,
                       environment: Dict[str, Any] = Depends(_page)):
    """Google Groups selection view."""
    config = get_config(tenant_id, environment_id)
    monitored = config.get("monitored_groups", [])
    delegated_email = config.get("delegated_admin_email", "")

    domain_groups = []
    error_msg = None
    try:
        client = WorkspaceClient(delegated_admin_email=delegated_email, sa_email=environment.get("sa_email"))
        domain_groups = client.list_domain_groups()
    except Exception as e:
        logger.error("Failed to query domain groups: %s", e)
        error_msg = str(e)

    return render(request, "license_sync/groups.html", {
        "active_page": "license-sync",
        "license_sync_page": "groups",
        "monitored_groups": monitored,
        "domain_groups": domain_groups,
        "error": error_msg,
    })


@router.get("/modules/license-sync/schedule", response_class=HTMLResponse)
async def schedule_view(request: Request, tenant_id: str, environment_id: str,
                         _: Dict[str, Any] = Depends(_page)):
    """Sync schedule configuration view."""
    config = get_config(tenant_id, environment_id)
    scheduler_service = SchedulerService()
    scheduler_status = scheduler_service.get_schedule()

    return render(request, "license_sync/schedule.html", {
        "active_page": "license-sync",
        "license_sync_page": "schedule",
        "config": config,
        "scheduler_status": scheduler_status,
    })


@router.get("/modules/license-sync/settings", response_class=HTMLResponse)
async def settings_view(request: Request, tenant_id: str, environment_id: str,
                         environment: Dict[str, Any] = Depends(_page)):
    """System settings, DWD connectivity test, and Gemini license subscription picker."""
    config = get_config(tenant_id, environment_id)
    license_configs: List[Dict[str, Any]] = []
    license_error: Optional[str] = None
    project_id = tenant_credentials.project_id_for(environment)
    try:
        license_configs = GeminiLicenseClient(
            project_id=project_id, sa_email=environment.get("sa_email")
        ).list_license_configs()
        if not license_configs:
            license_error = (
                "No Gemini Enterprise license subscriptions found in this project. "
                "Create one in the Gemini Enterprise console, or check that the "
                "service account has the Gemini Enterprise Admin role."
            )
    except Exception as e:
        logger.error("Failed to list Gemini license configs: %s", e)
        license_error = f"Could not list license subscriptions: {e}"

    return render(request, "license_sync/settings.html", {
        "active_page": "license-sync",
        "license_sync_page": "settings",
        "config": config,
        "license_configs": license_configs,
        "license_error": license_error,
    })


@router.get("/modules/license-sync/history", response_class=HTMLResponse)
async def history_view(request: Request, tenant_id: str, environment_id: str,
                        _: Dict[str, Any] = Depends(_page)):
    """Execution audit history view."""
    history = get_sync_history(tenant_id, environment_id, limit=50)
    return render(request, "license_sync/history.html", {
        "active_page": "license-sync",
        "license_sync_page": "history",
        "history": history,
    })


# -------------------------------------------------------------------------
# API Endpoints
# -------------------------------------------------------------------------
@router.post("/modules/license-sync/api/groups")
async def save_monitored_groups(payload: GroupsPayload, tenant_id: str, environment_id: str,
                                 _: Dict[str, Any] = Depends(_api)):
    """Save selected Google Groups to monitor in Firestore."""
    try:
        updated = update_config(tenant_id, environment_id, {"monitored_groups": payload.groups})
        return {
            "success": True,
            "message": f"Successfully updated monitored groups ({len(payload.groups)} selected).",
            "monitored_groups": updated.get("monitored_groups", [])
        }
    except Exception as e:
        logger.error("Error saving groups to Firestore: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/modules/license-sync/api/schedule")
async def update_sync_schedule(payload: SchedulePayload, tenant_id: str, environment_id: str,
                                _: Dict[str, Any] = Depends(_api)):
    """Update cron schedule in Firestore and programmatically in Cloud Scheduler."""
    cron = payload.cron_expression.strip()
    if not cron:
        raise HTTPException(status_code=400, detail="Cron expression cannot be empty.")

    # 1. Update Firestore config
    try:
        update_config(tenant_id, environment_id, {"cron_expression": cron})
    except Exception as e:
        logger.error("Failed to save schedule to Firestore: %s", e)

    # 2. Update Cloud Scheduler job
    #    NOTE: this still points at the single shared Cloud Scheduler job used
    #    before the multi-tenant conversion (see SchedulerService) - the
    #    per-environment scheduling fan-out is Phase 5, not yet built. Every
    #    environment's "Sync Schedule" page currently edits the same job.
    scheduler_svc = SchedulerService()
    sched_result = scheduler_svc.update_schedule(cron)

    return {
        "success": True,
        "cron_expression": cron,
        "cloud_scheduler": sched_result,
        "message": f"Schedule set to '{cron}' in Firestore. Cloud Scheduler: {sched_result.get('message')}"
    }


@router.post("/modules/license-sync/api/notifications")
async def update_notifications(payload: NotificationsPayload, tenant_id: str, environment_id: str,
                                _: Dict[str, Any] = Depends(_api)):
    """Save sync-run email notification settings to Firestore."""
    raw = payload.notification_emails
    if isinstance(raw, str):
        raw = raw.replace("\n", ",").split(",")
    emails = [e.strip() for e in raw if e and e.strip()]

    invalid = [e for e in emails if not _EMAIL_RE.match(e)]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Invalid email address(es): {', '.join(invalid)}")

    notify_on = payload.notify_on.strip().lower()
    if notify_on not in ("failures", "all"):
        raise HTTPException(status_code=400, detail="notify_on must be 'failures' or 'all'.")

    try:
        updated = update_config(tenant_id, environment_id, {
            "notification_emails": emails,
            "notify_on": notify_on,
        })
    except Exception as e:
        logger.error("Failed to save notification settings: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

    scope = "all runs" if notify_on == "all" else "failed & partial runs only"
    msg = (
        f"Notifications saved: {len(emails)} recipient(s), alerting on {scope}."
        if emails else "Notifications disabled (no recipients)."
    )
    return {
        "success": True,
        "message": msg,
        "notification_emails": updated.get("notification_emails", []),
        "notify_on": updated.get("notify_on", "failures"),
    }


@router.post("/modules/license-sync/api/settings")
async def save_settings(payload: SettingsPayload, tenant_id: str, environment_id: str,
                         environment: Dict[str, Any] = Depends(_api)):
    """Update the delegated admin and the selected Gemini Enterprise license subscription."""
    updates: Dict[str, Any] = {"delegated_admin_email": payload.delegated_admin_email.strip()}

    license_config = payload.license_config.strip()
    if license_config:
        if not gem_is_valid_config_name(license_config):
            raise HTTPException(
                status_code=400,
                detail=("Not a Gemini Enterprise license subscription "
                        "(projects/*/locations/*/licenseConfigs/*). Workspace product/SKU IDs are not supported."),
            )
        project_id = tenant_credentials.project_id_for(environment)
        try:
            available = {c["name"]: c for c in GeminiLicenseClient(
                project_id=project_id, sa_email=environment.get("sa_email")
            ).list_license_configs()}
        except Exception as e:
            logger.error("Could not validate license_config against the project: %s", e)
            raise HTTPException(status_code=502, detail=f"Could not verify the subscription: {e}")
        if license_config not in available:
            raise HTTPException(status_code=400, detail="That subscription does not exist in this project.")
        updates["license_config"] = license_config
        updates["license_label"] = available[license_config].get("label", license_config)
    else:
        updates["license_config"] = ""
        updates["license_label"] = ""

    try:
        updated = update_config(tenant_id, environment_id, updates)
    except Exception as e:
        logger.error("Failed to save settings: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

    return {"success": True, "message": "Settings saved successfully.", "config": updated}


@router.post("/modules/license-sync/api/test-connection")
async def test_dwd_connection(payload: DwdTestPayload, tenant_id: str, environment_id: str,
                               environment: Dict[str, Any] = Depends(_api)):
    """Perform live connectivity check against Admin SDK Directory API using DWD."""
    client = WorkspaceClient(delegated_admin_email=payload.delegated_admin_email, sa_email=environment.get("sa_email"))
    result = client.test_dwd_connection(payload.delegated_admin_email)
    return result


@router.post("/modules/license-sync/api/sync/run")
async def trigger_sync(request: Request, tenant_id: str, environment_id: str,
                        payload: Optional[SyncTriggerPayload] = None,
                        _: Dict[str, Any] = Depends(_api)):
    """Manual sync worker trigger endpoint (the dashboard's "Run Sync Now"
    button). Scheduled runs do not go through this HTTP route - Cloud
    Scheduler calls the Cloud Run job directly via the Cloud Run Admin API."""
    triggered_by = (payload.triggered_by if payload and payload.triggered_by else "admin_ui")
    logger.info("Executing license sync for tenant=%s environment=%s (trigger source: %s)",
                tenant_id, environment_id, triggered_by)

    try:
        result = run_license_sync(tenant_id, environment_id, triggered_by=triggered_by)
        return JSONResponse(content=result, status_code=status.HTTP_200_OK)
    except Exception as e:
        logger.critical("Sync engine crashed: %s", e, exc_info=True)
        return JSONResponse(
            content={
                "status": "FAILED",
                "triggered_by": triggered_by,
                "error": str(e),
                "message": "Sync engine encountered an unhandled exception."
            },
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
