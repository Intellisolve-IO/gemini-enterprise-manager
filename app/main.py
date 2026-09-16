import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.firestore_db import get_config, update_config
from app import auth
from app.core.module_auth import ModuleDisabledError
from app.core.templating import templates
from app.landing import router as landing_router
from app.modules.health_check.router import router as health_check_router
from app.modules.license_sync.router import router as license_sync_router
from app.modules.url_mapping.router import router as url_mapping_router

# Configure logging
logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("gemini_provisioner.main")

# When access control is enforced, hide the interactive API docs / schema behind
# IAP is still not enough - drop them entirely so only real endpoints are exposed.
_docs_kwargs = {} if not settings.AUTH_ENABLED else {
    "docs_url": None, "redoc_url": None, "openapi_url": None,
}

app = FastAPI(
    title="Gemini Enterprise Admin Console",
    description="Modular admin tooling for Gemini Enterprise: license sync, health "
                "checks, app URL mapping, agent deployment, and more.",
    version="2.0.0",
    **_docs_kwargs,
)

# Paths for static files
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
# Expose deployment identifiers to every template (shown in the header/footer).
templates.env.globals["gcp_project_id"] = settings.GCP_PROJECT_ID or "unset"
templates.env.globals["gcp_region"] = settings.GCP_REGION

# Last public base URL persisted to Firestore config (per process cache, so we
# only write when it actually changes).
_seen_base_url: Optional[str] = None


@app.middleware("http")
async def capture_base_url(request: Request, call_next):
    """Learn this service's public base URL from real traffic so scheduler-triggered
    runs (which have no request) can build absolute links in notification emails.

    Only ``*.run.app`` hosts are auto-trusted (the client-controlled Host header
    could otherwise poison the link). For a custom domain, set ``PUBLIC_BASE_URL``.
    """
    global _seen_base_url
    # Log the IAP JWT audience even before enforcement is turned on, so the exact
    # value for IAP_AUDIENCE is discoverable from the logs. No-op without the header.
    _assertion = request.headers.get(settings.IAP_JWT_HEADER)
    if _assertion:
        auth._log_observed_audience(_assertion)
    try:
        if not settings.PUBLIC_BASE_URL and request.method == "GET" and \
                not request.url.path.startswith(("/static", "/healthz", "/api")):
            base = str(request.base_url).rstrip("/")
            host = request.url.hostname or ""
            trusted = host.endswith(".run.app") or host in ("localhost", "127.0.0.1")
            if base and trusted and base != _seen_base_url:
                _seen_base_url = base
                if get_config().get("public_base_url") != base:
                    update_config({"public_base_url": base})
    except Exception as e:  # never break a request over this
        logger.debug("base URL capture skipped: %s", e)
    return await call_next(request)


@app.exception_handler(ModuleDisabledError)
async def module_disabled_handler(request: Request, exc: ModuleDisabledError):
    """A disabled module's page route redirects to the landing page with a toast."""
    return RedirectResponse(url=f"/?disabled_module={exc.module_id}", status_code=303)


# -------------------------------------------------------------------------
# Module routers
# -------------------------------------------------------------------------
app.include_router(landing_router)
app.include_router(license_sync_router)
app.include_router(health_check_router)
app.include_router(url_mapping_router)


@app.get("/healthz")
async def healthz():
    """Liveness/readiness probe for Cloud Run."""
    return {"status": "healthy", "service": "gemini-enterprise-admin-console"}
