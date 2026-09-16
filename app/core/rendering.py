"""Thin wrapper around ``Jinja2Templates.TemplateResponse`` that injects the
current environment's enabled-module list into every page's context, so
``base.html``'s nav can be data-driven without every route having to remember
to pass it in.

Reads ``tenant_id``/``environment_id`` off ``request.state`` (set by the
``require_environment`` dependency - see ``app/core/session_auth.py``) when
present; pages rendered without an environment in scope (sign-in, the tenant
picker, onboarding) simply get an empty module list, which is correct - there's
nothing environment-scoped to link to yet.
"""
from typing import Any, Dict

from fastapi import Request
from fastapi.responses import HTMLResponse

from app.core.modules import enabled_modules_context
from app.core.templating import templates


def render(request: Request, name: str, context: Dict[str, Any]) -> HTMLResponse:
    tenant_id = getattr(request.state, "tenant_id", None)
    environment_id = getattr(request.state, "environment_id", None)
    modules = enabled_modules_context(tenant_id, environment_id) if tenant_id and environment_id else []
    firebase_user = getattr(request.state, "firebase_user", None)
    merged = {
        "enabled_modules": modules,
        "tenant_id": tenant_id,
        "environment_id": environment_id,
        "principal": (firebase_user or {}).get("email"),
        **context,
    }
    return templates.TemplateResponse(request=request, name=name, context=merged)
