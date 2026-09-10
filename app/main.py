import logging
import os
from typing import Dict, Any, List, Optional
from pathlib import Path

from fastapi import FastAPI, Request, Form, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.config import settings
from app.firestore_db import get_config, update_config, get_sync_history
from app.workspace_client import WorkspaceClient
from app.sync_worker import run_license_sync
from app.scheduler_service import SchedulerService

# Configure logging
logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("gemini_provisioner.main")

app = FastAPI(
    title="Gemini Enterprise License Provisioner",
    description="Automate Gemini Enterprise license assignment based on Google Groups membership.",
    version="1.0.0"
)

# Paths for static and templates
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
# Expose deployment identifiers to every template (shown in the header/footer).
templates.env.globals["gcp_project_id"] = settings.GCP_PROJECT_ID or "unset"
templates.env.globals["gcp_region"] = settings.GCP_REGION


# -------------------------------------------------------------------------
# Request Schemas
# -------------------------------------------------------------------------
class GroupsPayload(BaseModel):
    groups: List[str]


class SchedulePayload(BaseModel):
    cron_expression: str


class SettingsPayload(BaseModel):
    delegated_admin_email: str
    product_id: str
    sku_id: str


class SyncTriggerPayload(BaseModel):
    triggered_by: Optional[str] = "admin_ui"


class DwdTestPayload(BaseModel):
    delegated_admin_email: Optional[str] = None


# -------------------------------------------------------------------------
# HTML Views
# -------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def dashboard_view(request: Request):
    """Admin Dashboard Homepage."""
    config = get_config()
    history = get_sync_history(limit=1)
    last_run = history[0] if history else None
    
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "active_page": "dashboard",
            "config": config,
            "last_run": last_run
        }
    )


@app.get("/groups", response_class=HTMLResponse)
async def groups_view(request: Request):
    """Google Groups selection view."""
    config = get_config()
    monitored = config.get("monitored_groups", [])
    delegated_email = config.get("delegated_admin_email", settings.DELEGATED_ADMIN_EMAIL)
    
    domain_groups = []
    error_msg = None
    try:
        client = WorkspaceClient(delegated_admin_email=delegated_email)
        domain_groups = client.list_domain_groups()
    except Exception as e:
        logger.error("Failed to query domain groups: %s", e)
        error_msg = str(e)

    return templates.TemplateResponse(
        request=request,
        name="groups.html",
        context={
            "active_page": "groups",
            "monitored_groups": monitored,
            "domain_groups": domain_groups,
            "error": error_msg
        }
    )


@app.get("/schedule", response_class=HTMLResponse)
async def schedule_view(request: Request):
    """Sync schedule configuration view."""
    config = get_config()
    scheduler_service = SchedulerService()
    scheduler_status = scheduler_service.get_schedule()

    return templates.TemplateResponse(
        request=request,
        name="schedule.html",
        context={
            "active_page": "schedule",
            "config": config,
            "scheduler_status": scheduler_status
        }
    )


@app.get("/settings", response_class=HTMLResponse)
async def settings_view(request: Request):
    """System settings and DWD connectivity test view."""
    config = get_config()
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "active_page": "settings",
            "config": config
        }
    )


@app.get("/history", response_class=HTMLResponse)
async def history_view(request: Request):
    """Execution audit history view."""
    history = get_sync_history(limit=50)
    return templates.TemplateResponse(
        request=request,
        name="history.html",
        context={
            "active_page": "history",
            "history": history
        }
    )


# -------------------------------------------------------------------------
# API Endpoints
# -------------------------------------------------------------------------
@app.post("/api/groups")
async def save_monitored_groups(payload: GroupsPayload):
    """Save selected Google Groups to monitor in Firestore."""
    try:
        updated = update_config({"monitored_groups": payload.groups})
        return {
            "success": True,
            "message": f"Successfully updated monitored groups ({len(payload.groups)} selected).",
            "monitored_groups": updated.get("monitored_groups", [])
        }
    except Exception as e:
        logger.error("Error saving groups to Firestore: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/schedule")
async def update_sync_schedule(payload: SchedulePayload):
    """Update cron schedule in Firestore and programmatically in Cloud Scheduler."""
    cron = payload.cron_expression.strip()
    if not cron:
        raise HTTPException(status_code=400, detail="Cron expression cannot be empty.")

    # 1. Update Firestore config
    try:
        update_config({"cron_expression": cron})
    except Exception as e:
        logger.error("Failed to save schedule to Firestore: %s", e)

    # 2. Update Cloud Scheduler job
    scheduler_svc = SchedulerService()
    sched_result = scheduler_svc.update_schedule(cron)

    return {
        "success": True,
        "cron_expression": cron,
        "cloud_scheduler": sched_result,
        "message": f"Schedule set to '{cron}' in Firestore. Cloud Scheduler: {sched_result.get('message')}"
    }


@app.post("/api/settings")
async def save_settings(payload: SettingsPayload):
    """Update Delegated Admin Email, Product ID, and SKU ID in Firestore."""
    try:
        updated = update_config({
            "delegated_admin_email": payload.delegated_admin_email.strip(),
            "product_id": payload.product_id.strip(),
            "sku_id": payload.sku_id.strip(),
        })
        return {
            "success": True,
            "message": "Settings saved successfully.",
            "config": updated
        }
    except Exception as e:
        logger.error("Failed to save settings: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/test-connection")
async def test_dwd_connection(payload: DwdTestPayload):
    """Perform live connectivity check against Admin SDK Directory API using DWD."""
    client = WorkspaceClient(delegated_admin_email=payload.delegated_admin_email)
    result = client.test_dwd_connection(payload.delegated_admin_email)
    return result


@app.post("/api/sync/run")
async def trigger_sync(request: Request, payload: Optional[SyncTriggerPayload] = None):
    """Scheduled & manual sync worker trigger endpoint.
    
    Invoked by:
    - Cloud Scheduler via HTTP POST with OIDC authentication
    - Admin UI manually via JSON POST
    """
    # Identify trigger source
    auth_header = request.headers.get("Authorization", "")
    user_agent = request.headers.get("User-Agent", "")
    
    triggered_by = "admin_ui"
    if "Google-Cloud-Scheduler" in user_agent or "Bearer" in auth_header:
        triggered_by = "scheduled"
    elif payload and payload.triggered_by:
        triggered_by = payload.triggered_by

    logger.info("Executing license sync endpoint (trigger source: %s)", triggered_by)

    try:
        result = run_license_sync(triggered_by=triggered_by)
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


@app.get("/healthz")
async def healthz():
    """Liveness/readiness probe for Cloud Run."""
    return {"status": "healthy", "service": "gemini-license-provisioner"}
