"""The consolidated landing page (`GET /`) and the module on/off toggle API.

Every other page's route lives in its own module's router; this is the one route
that spans all of them, so it stays at the top level rather than under app/modules/.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.auth import require_super_admin
from app.core.module_config import get_module_config, update_module_config
from app.core.modules import MODULES, get_module
from app.core.rendering import render

logger = logging.getLogger("gemini_provisioner.landing")

router = APIRouter()


class ToggleModulePayload(BaseModel):
    enabled: bool


@router.get("/", response_class=HTMLResponse)
async def landing_view(request: Request, principal: Optional[str] = Depends(require_super_admin)):
    """The module grid: every registered module, its enabled state, and a toggle."""
    cards = []
    for m in MODULES:
        cfg = get_module_config(m.id, {"enabled": m.default_enabled})
        cards.append({
            "id": m.id,
            "title": m.title,
            "icon": m.icon,
            "description": m.description,
            "base_path": m.base_path,
            "enabled": bool(cfg.get("enabled")),
        })

    return render(request, "landing.html", {
        "active_page": "landing",
        "modules": cards,
        "principal": principal,
    })


@router.post("/api/modules/{module_id}/toggle")
async def toggle_module(
    module_id: str,
    payload: ToggleModulePayload,
    _: Optional[str] = Depends(require_super_admin),
):
    """Flip a module's Firestore `enabled` flag. No redeploy required."""
    meta = get_module(module_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Unknown module: {module_id!r}")

    try:
        updated = update_module_config(
            module_id, {"enabled": payload.enabled}, defaults={"enabled": meta.default_enabled}
        )
    except Exception as e:
        logger.error("Failed to toggle module %r: %s", module_id, e)
        raise HTTPException(status_code=500, detail=str(e))

    return {"success": True, "module_id": module_id, "enabled": bool(updated.get("enabled"))}
