"""FastAPI dependencies that gate a module's routes on it being enabled for
the current tenant's environment, layered on top of ``require_environment``
(auth + tenant membership + environment existence are always checked first,
so a non-member can never learn whether a module is enabled).

Page routes and API routes fail differently on a disabled module: a page
redirects back to the environment's landing page with an explanatory toast;
a JSON API returns a plain 403 for the calling JS to surface as a toast
itself. See ``ModuleDisabledError`` and its handler registered in ``app/main.py``.
"""
from typing import Any, Dict

from fastapi import Depends, HTTPException, Request, status

from app.core.module_config import is_module_enabled
from app.core.session_auth import require_environment


class ModuleDisabledError(Exception):
    """Raised by a disabled page-route dependency; translated to a redirect by
    the exception handler registered in app/main.py."""

    def __init__(self, tenant_id: str, environment_id: str, module_id: str):
        self.tenant_id = tenant_id
        self.environment_id = environment_id
        self.module_id = module_id


def require_module_enabled_page(module_id: str):
    """Dependency factory for GET page routes belonging to `module_id`."""
    env_dep = require_environment()

    def _dep(request: Request, environment: Dict[str, Any] = Depends(env_dep)) -> Dict[str, Any]:
        if not is_module_enabled(request.state.tenant_id, request.state.environment_id, module_id):
            raise ModuleDisabledError(request.state.tenant_id, request.state.environment_id, module_id)
        return environment

    return _dep


def require_module_enabled_api(module_id: str):
    """Dependency factory for POST/JSON API routes belonging to `module_id`."""
    env_dep = require_environment()

    def _dep(request: Request, environment: Dict[str, Any] = Depends(env_dep)) -> Dict[str, Any]:
        if not is_module_enabled(request.state.tenant_id, request.state.environment_id, module_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This module is disabled.")
        return environment

    return _dep
