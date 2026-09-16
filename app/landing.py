"""Post-login navigation: sign-in gate, tenant picker/creation, environment
picker/creation, and the consolidated per-environment module grid.

This is deliberately minimal - just enough to create a tenant and an
environment and reach the module grid for manual testing. The full guided
onboarding wizard (service-account setup, connection verification, topology
choices, default-config deployment) is a separate, later piece of work.
"""
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from app.core import tenants as tenants_db
from app.core.module_config import get_module_config, update_module_config
from app.core.modules import MODULES, get_module
from app.core.rendering import render
from app.core.session_auth import (
    current_firebase_user,
    optional_firebase_user,
    require_environment,
    require_tenant_member,
)

logger = logging.getLogger("gemini_provisioner.landing")

router = APIRouter()


class CreateTenantPayload(BaseModel):
    name: str
    primary_domain: str = ""


class CreateEnvironmentPayload(BaseModel):
    display_name: str


class ToggleModulePayload(BaseModel):
    enabled: bool


# -------------------------------------------------------------------------
# "/" - sign-in gate + tenant picker
# -------------------------------------------------------------------------
@router.get("/", response_class=HTMLResponse)
async def root_view(request: Request, user: Optional[Dict[str, Any]] = Depends(optional_firebase_user)):
    if user is None:
        return render(request, "sign_in.html", {})

    my_tenants = tenants_db.list_tenants_for_uid(user["uid"])
    if len(my_tenants) == 1:
        return RedirectResponse(url=f"/t/{my_tenants[0]['id']}/", status_code=303)

    return render(request, "tenant_picker.html", {"tenants": my_tenants})


@router.post("/tenants")
async def create_tenant(payload: CreateTenantPayload,
                         user: Dict[str, Any] = Depends(current_firebase_user)):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="A company/tenant name is required.")

    tenant_id = tenants_db.create_tenant(name, created_by_uid=user["uid"],
                                          primary_domain=payload.primary_domain.strip())
    tenants_db.add_member(tenant_id, user["uid"], user.get("email", ""), role="owner",
                           added_by_uid=user["uid"])
    return {"success": True, "tenant_id": tenant_id}


# -------------------------------------------------------------------------
# "/t/{tenant_id}/" - environment picker, scoped to one tenant
# -------------------------------------------------------------------------
@router.get("/t/{tenant_id}/", response_class=HTMLResponse)
async def tenant_view(request: Request, tenant_id: str,
                       member: Dict[str, Any] = Depends(require_tenant_member())):
    tenant = tenants_db.get_tenant(tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found.")

    environments = tenants_db.list_environments(tenant_id)
    if len(environments) == 1:
        return RedirectResponse(url=f"/t/{tenant_id}/e/{environments[0]['id']}/", status_code=303)

    return render(request, "environment_picker.html", {
        "tenant": tenant,
        "environments": environments,
    })


@router.post("/t/{tenant_id}/environments")
async def create_environment(payload: CreateEnvironmentPayload, tenant_id: str,
                              _: Dict[str, Any] = Depends(require_tenant_member())):
    display_name = payload.display_name.strip()
    if not display_name:
        raise HTTPException(status_code=400, detail="An environment name is required.")

    environment_id = tenants_db.create_environment(tenant_id, display_name)
    return {"success": True, "environment_id": environment_id}


# -------------------------------------------------------------------------
# "/t/{tenant_id}/e/{environment_id}/" - the module grid
# -------------------------------------------------------------------------
@router.get("/t/{tenant_id}/e/{environment_id}/", response_class=HTMLResponse)
async def environment_landing_view(request: Request, tenant_id: str, environment_id: str,
                                    _: Dict[str, Any] = Depends(require_environment())):
    cards = []
    for m in MODULES:
        cfg = get_module_config(tenant_id, environment_id, m.id, {"enabled": m.default_enabled})
        cards.append({
            "id": m.id,
            "title": m.title,
            "icon": m.icon,
            "description": m.description,
            "base_path": m.base_path(tenant_id, environment_id),
            "enabled": bool(cfg.get("enabled")),
        })

    return render(request, "landing.html", {
        "active_page": "landing",
        "modules": cards,
    })


@router.post("/t/{tenant_id}/e/{environment_id}/api/modules/{module_id}/toggle")
async def toggle_module(module_id: str, payload: ToggleModulePayload, tenant_id: str, environment_id: str,
                         _: Dict[str, Any] = Depends(require_environment())):
    """Flip a module's Firestore `enabled` flag for this environment. No
    redeploy required."""
    meta = get_module(module_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Unknown module: {module_id!r}")

    try:
        updated = update_module_config(
            tenant_id, environment_id, module_id,
            {"enabled": payload.enabled}, defaults={"enabled": meta.default_enabled},
        )
    except Exception as e:
        logger.error("Failed to toggle module %r for tenant=%s environment=%s: %s",
                      module_id, tenant_id, environment_id, e)
        raise HTTPException(status_code=500, detail=str(e))

    return {"success": True, "module_id": module_id, "enabled": bool(updated.get("enabled"))}
