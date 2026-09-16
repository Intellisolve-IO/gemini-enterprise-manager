"""FastAPI dependencies that gate a module's routes on it being enabled, layered
on top of the existing ``require_super_admin`` check (auth is always evaluated
first, so a non-admin can never learn whether a module is enabled).

Page routes and API routes fail differently on a disabled module: a page redirects
back to the landing page with an explanatory toast; a JSON API returns a plain 403
for the calling JS to surface as a toast itself. See ``ModuleDisabledError`` and its
handler registered in ``app/main.py``.
"""
from typing import Optional

from fastapi import Depends, HTTPException, Request, status

from app.auth import require_super_admin
from app.core.module_config import is_module_enabled


class ModuleDisabledError(Exception):
    """Raised by a disabled page-route dependency; translated to a redirect by the
    exception handler registered in app/main.py."""

    def __init__(self, module_id: str):
        self.module_id = module_id


def require_module_enabled_page(module_id: str):
    """Dependency factory for GET page routes belonging to `module_id`."""

    def _dep(request: Request, principal: Optional[str] = Depends(require_super_admin)):
        if not is_module_enabled(module_id):
            raise ModuleDisabledError(module_id)
        return principal

    return _dep


def require_module_enabled_api(module_id: str):
    """Dependency factory for POST/JSON API routes belonging to `module_id`."""

    def _dep(request: Request, principal: Optional[str] = Depends(require_super_admin)):
        if not is_module_enabled(module_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This module is disabled.")
        return principal

    return _dep
