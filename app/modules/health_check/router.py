import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.core.module_auth import require_module_enabled_api, require_module_enabled_page
from app.core.module_config import update_module_config
from app.core.rendering import render
from app.modules.health_check.checks import run_all_checks

logger = logging.getLogger("gemini_provisioner.health_check")

router = APIRouter()

_MODULE_ID = "health-check"
_DEFAULTS = {"enabled": False, "last_report": None, "last_run_at": None}

_page = require_module_enabled_page(_MODULE_ID)
_api = require_module_enabled_api(_MODULE_ID)


def _run_and_cache(tenant_id: str, environment_id: str) -> dict:
    report = run_all_checks(tenant_id, environment_id)
    try:
        update_module_config(
            tenant_id, environment_id, _MODULE_ID,
            {"last_report": report, "last_run_at": report["ran_at"]},
            _DEFAULTS,
        )
    except Exception as e:  # caching failure should never block showing the report
        logger.warning("Failed to cache health check report: %s", e)
    return report


@router.get("/modules/health-check", response_class=HTMLResponse)
async def health_check_view(request: Request, tenant_id: str, environment_id: str,
                             _: Dict[str, Any] = Depends(_page)):
    report = _run_and_cache(tenant_id, environment_id)
    return render(request, "health_check/report.html", {
        "active_page": _MODULE_ID,
        "report": report,
    })


@router.post("/modules/health-check/api/run")
async def health_check_run(tenant_id: str, environment_id: str,
                            _: Dict[str, Any] = Depends(_api)):
    """Re-run all checks and return the fresh report as JSON (used by the page's
    "Re-run" button so it doesn't need a full reload)."""
    return _run_and_cache(tenant_id, environment_id)
