import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.config import settings
from app.core.module_auth import require_module_enabled_api, require_module_enabled_page
from app.core.module_config import get_module_config, update_module_config
from app.core.rendering import render
from app.modules.url_mapping import compute_client

logger = logging.getLogger("gemini_provisioner.url_mapping")

router = APIRouter()

_MODULE_ID = "url-mapping"
_DEFAULTS = {"enabled": False, "mappings": []}

_page = require_module_enabled_page(_MODULE_ID)
_api = require_module_enabled_api(_MODULE_ID)


class CreateMappingPayload(BaseModel):
    custom_domain: str
    target_deep_link: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_mappings(tenant_id: str, environment_id: str) -> List[Dict[str, Any]]:
    return get_module_config(tenant_id, environment_id, _MODULE_ID, _DEFAULTS).get("mappings", [])


def _save_mappings(tenant_id: str, environment_id: str, mappings: List[Dict[str, Any]]) -> None:
    update_module_config(tenant_id, environment_id, _MODULE_ID, {"mappings": mappings}, _DEFAULTS)


@router.get("/modules/url-mapping", response_class=HTMLResponse)
async def url_mapping_view(request: Request, tenant_id: str, environment_id: str,
                            _: Dict[str, Any] = Depends(_page)):
    return render(request, "url_mapping/index.html", {
        "active_page": _MODULE_ID,
        "mappings": _get_mappings(tenant_id, environment_id),
    })


@router.post("/modules/url-mapping/api/mappings")
async def create_mapping(payload: CreateMappingPayload, request: Request, tenant_id: str, environment_id: str,
                          _: Dict[str, Any] = Depends(_api)):
    custom_domain = payload.custom_domain.strip().lower()
    target_deep_link = payload.target_deep_link.strip()
    if not custom_domain:
        raise HTTPException(status_code=400, detail="custom_domain is required.")
    try:
        compute_client.validate_target_url(target_deep_link)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    firebase_user = getattr(request.state, "firebase_user", None) or {}
    mapping_id = uuid.uuid4().hex[:12]
    mapping: Dict[str, Any] = {
        "id": mapping_id,
        "custom_domain": custom_domain,
        "target_deep_link": target_deep_link,
        "created_by": firebase_user.get("email", "unknown"),
        "created_at": _now(),
        "status": "provisioning",
        "gcp_resources": {},
        "last_checked_at": None,
        "last_error": None,
    }
    mappings = _get_mappings(tenant_id, environment_id)
    mappings.append(mapping)
    _save_mappings(tenant_id, environment_id, mappings)

    # NOTE: still provisions against the central app's own GCP project
    # (settings.GCP_PROJECT_ID), not yet the environment's own tenant-owned
    # project via impersonated credentials - that's Phase 2 of the
    # multi-tenant conversion (see app/sync_worker.py's docstring).
    try:
        mapping["gcp_resources"] = compute_client.provision_mapping(
            settings.GCP_PROJECT_ID, mapping_id, custom_domain, target_deep_link
        )
        mapping["status"] = "provisioning"  # cert provisioning continues async; see /refresh
    except Exception as e:
        logger.error("Failed to provision URL mapping %s: %s", mapping_id, e)
        mapping["status"] = "failed"
        mapping["last_error"] = str(e)

    mapping["last_checked_at"] = _now()
    _save_mappings(tenant_id, environment_id, mappings)  # `mapping` is the same object referenced inside `mappings`

    return {"success": mapping["status"] != "failed", "mapping": mapping}


@router.post("/modules/url-mapping/api/mappings/{mapping_id}/refresh")
async def refresh_mapping(mapping_id: str, tenant_id: str, environment_id: str,
                           _: Dict[str, Any] = Depends(_api)):
    mappings = _get_mappings(tenant_id, environment_id)
    mapping = next((m for m in mappings if m["id"] == mapping_id), None)
    if mapping is None:
        raise HTTPException(status_code=404, detail="Mapping not found.")

    cert_name = mapping.get("gcp_resources", {}).get("ssl_cert_name")
    if cert_name:
        try:
            cert_status = compute_client.get_certificate_status(settings.GCP_PROJECT_ID, cert_name)
            mapping["gcp_resources"]["cert_status"] = cert_status
            if cert_status == compute_client.CERT_STATUS_ACTIVE:
                mapping["status"] = "active"
            elif cert_status in compute_client.CERT_STATUSES_FAILED:
                mapping["status"] = "failed"
            else:
                mapping["status"] = "provisioning"
            mapping["last_error"] = None
        except Exception as e:
            mapping["last_error"] = str(e)
    mapping["last_checked_at"] = _now()
    _save_mappings(tenant_id, environment_id, mappings)
    return {"success": True, "mapping": mapping}


@router.post("/modules/url-mapping/api/mappings/{mapping_id}/delete")
async def delete_mapping(mapping_id: str, tenant_id: str, environment_id: str,
                          _: Dict[str, Any] = Depends(_api)):
    mappings = _get_mappings(tenant_id, environment_id)
    mapping = next((m for m in mappings if m["id"] == mapping_id), None)
    if mapping is None:
        raise HTTPException(status_code=404, detail="Mapping not found.")

    try:
        compute_client.teardown_mapping(settings.GCP_PROJECT_ID, mapping.get("gcp_resources", {}))
    except Exception as e:
        logger.error("Failed to tear down URL mapping %s: %s", mapping_id, e)
        raise HTTPException(status_code=500, detail=f"Failed to delete GCP resources: {e}")

    remaining = [m for m in mappings if m["id"] != mapping_id]
    _save_mappings(tenant_id, environment_id, remaining)
    return {"success": True}
