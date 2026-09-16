import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.auth_routes import router as auth_router
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

# Hide the interactive API docs / schema in anything but local debug - this is
# now a public multi-tenant SaaS surface, not a single-tenant IAP-fronted
# deployment, so the docs stay hidden by default.
_docs_kwargs = {} if settings.DEBUG else {
    "docs_url": None, "redoc_url": None, "openapi_url": None,
}

app = FastAPI(
    title="Gemini Enterprise Admin Console",
    description="Modular admin tooling for Gemini Enterprise: license sync, health "
                "checks, app URL mapping, agent deployment, and more.",
    version="3.0.0",
    **_docs_kwargs,
)

# Paths for static files
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
# Expose deployment identifiers to every template (shown in the header/footer).
templates.env.globals["gcp_project_id"] = settings.GCP_PROJECT_ID or "unset"
templates.env.globals["gcp_region"] = settings.GCP_REGION
templates.env.globals["firebase_api_key"] = settings.FIREBASE_API_KEY or ""
templates.env.globals["firebase_auth_domain"] = settings.FIREBASE_AUTH_DOMAIN
templates.env.globals["firebase_project_id"] = settings.FIREBASE_PROJECT_ID or ""


@app.exception_handler(ModuleDisabledError)
async def module_disabled_handler(request: Request, exc: ModuleDisabledError):
    """A disabled module's page route redirects to the environment's landing
    page with an explanatory toast."""
    url = f"/t/{exc.tenant_id}/e/{exc.environment_id}/?disabled_module={exc.module_id}"
    return RedirectResponse(url=url, status_code=303)


# -------------------------------------------------------------------------
# Routers
# -------------------------------------------------------------------------
# auth_router and landing_router define their own paths (no shared prefix -
# landing_router itself spans "/", "/t/{tenant_id}/", and
# "/t/{tenant_id}/e/{environment_id}/"). Every feature module's routes are
# environment-scoped, so they share one prefix mounted here rather than each
# repeating "/t/{tenant_id}/e/{environment_id}" in their own decorators.
_ENVIRONMENT_PREFIX = "/t/{tenant_id}/e/{environment_id}"

app.include_router(auth_router)
app.include_router(landing_router)
app.include_router(license_sync_router, prefix=_ENVIRONMENT_PREFIX)
app.include_router(health_check_router, prefix=_ENVIRONMENT_PREFIX)
app.include_router(url_mapping_router, prefix=_ENVIRONMENT_PREFIX)


@app.get("/healthz")
async def healthz():
    """Liveness/readiness probe for Cloud Run."""
    return {"status": "healthy", "service": "gemini-enterprise-admin-console"}
