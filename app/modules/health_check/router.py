import logging
from typing import Optional

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


def _run_and_cache() -> dict:
    report = run_all_checks()
    try:
        update_module_config(
            _MODULE_ID,
            {"last_report": report, "last_run_at": report["ran_at"]},
            _DEFAULTS,
        )
    except Exception as e:  # caching failure should never block showing the report
        logger.warning("Failed to cache health check report: %s", e)
    return report


@router.get("/modules/health-check", response_class=HTMLResponse)
async def health_check_view(
    request: Request,
    principal: Optional[str] = Depends(require_module_enabled_page(_MODULE_ID)),
):
    report = _run_and_cache()
    return render(request, "health_check/report.html", {
        "active_page": _MODULE_ID,
        "report": report,
        "principal": principal,
    })


@router.post("/modules/health-check/api/run")
async def health_check_run(
    _: Optional[str] = Depends(require_module_enabled_api(_MODULE_ID)),
):
    """Re-run all checks and return the fresh report as JSON (used by the page's
    "Re-run" button so it doesn't need a full reload)."""
    return _run_and_cache()
