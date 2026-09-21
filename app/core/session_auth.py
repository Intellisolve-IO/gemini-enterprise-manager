"""Session-cookie authentication (replacing IAP) and tenant/environment
membership authorization.

Every authenticated page/API depends on `current_firebase_user`, which reads
and verifies the `ge_session` cookie set by `POST /auth/session` (see
app/auth_routes.py). Tenant- and environment-scoped routes additionally
depend on `require_tenant_member`/`require_tenant_admin`/`require_environment`,
which read `tenant_id`/`environment_id` off the request's path params (set by
FastAPI's routing before any dependency runs) and check Firestore membership.

Revocation checks (`check_revoked=True`) cost a network round-trip per call,
so normal traffic skips them; only call `current_firebase_user_strict` around
sensitive actions (membership/role changes) where a just-revoked session must
be rejected immediately rather than at its next natural refresh.
"""
import logging
from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException, Request, status

from app.core import tenants as tenants_db
from app.core.firebase_auth import verify_session_cookie

logger = logging.getLogger("gemini_provisioner.session_auth")

SESSION_COOKIE_NAME = "ge_session"


def _verify_cookie(request: Request, check_revoked: bool) -> Dict[str, Any]:
    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if not cookie:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not signed in.")
    try:
        claims = verify_session_cookie(cookie, check_revoked=check_revoked)
    except Exception as e:
        logger.info("Session cookie verification failed: %s", e)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired or invalid.")
    return claims


def current_firebase_user(request: Request) -> Dict[str, Any]:
    """FastAPI dependency: the signed-in user's Firebase claims (uid, email, ...).
    401s if there's no valid session. Cached on request.state so multiple
    dependencies in the same request chain don't re-verify the cookie."""
    if not hasattr(request.state, "firebase_user"):
        request.state.firebase_user = _verify_cookie(request, check_revoked=False)
    return request.state.firebase_user


def current_firebase_user_strict(request: Request) -> Dict[str, Any]:
    """Same as `current_firebase_user`, but checks token revocation - use only
    around sensitive actions (see module docstring)."""
    claims = _verify_cookie(request, check_revoked=True)
    request.state.firebase_user = claims
    return claims


def optional_firebase_user(request: Request) -> Optional[Dict[str, Any]]:
    """Like `current_firebase_user`, but returns None instead of 401ing - for
    pages (like the sign-in screen itself) that render differently when
    already signed in, without requiring it."""
    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if not cookie:
        return None
    try:
        claims = verify_session_cookie(cookie, check_revoked=False)
    except Exception:
        return None
    request.state.firebase_user = claims
    return claims


def require_tenant_member(tenant_id_param: str = "tenant_id"):
    """Dependency factory: 403s unless the signed-in user is a member of the
    tenant named by the `tenant_id` path parameter. Sets request.state.tenant_id
    and .tenant_role for downstream use."""

    def _dep(request: Request, user: Dict[str, Any] = Depends(current_firebase_user)) -> Dict[str, Any]:
        tenant_id = request.path_params.get(tenant_id_param)
        if not tenant_id:
            raise HTTPException(status_code=400, detail=f"Missing path parameter: {tenant_id_param!r}")
        member = tenants_db.get_member(tenant_id, user["uid"])
        if member is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                 detail="Not a member of this tenant.")
        request.state.tenant_id = tenant_id
        request.state.tenant_role = member["role"]
        return member

    return _dep


def require_tenant_admin(tenant_id_param: str = "tenant_id"):
    """Dependency factory: like `require_tenant_member`, but additionally
    requires the "owner" or "admin" role."""
    member_dep = require_tenant_member(tenant_id_param)

    def _dep(request: Request, member: Dict[str, Any] = Depends(member_dep)) -> Dict[str, Any]:
        if member.get("role") not in ("owner", "admin"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                 detail="Requires tenant admin access.")
        return member

    return _dep


def require_environment(tenant_id_param: str = "tenant_id", environment_id_param: str = "environment_id"):
    """Dependency factory: requires tenant membership (see `require_tenant_member`)
    AND that the environment named by the `environment_id` path parameter
    exists under that tenant. Sets request.state.environment_id and .environment."""
    member_dep = require_tenant_member(tenant_id_param)

    def _dep(request: Request, member: Dict[str, Any] = Depends(member_dep)) -> Dict[str, Any]:
        environment_id = request.path_params.get(environment_id_param)
        if not environment_id:
            raise HTTPException(status_code=400, detail=f"Missing path parameter: {environment_id_param!r}")
        environment = tenants_db.get_environment(request.state.tenant_id, environment_id)
        if environment is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Environment not found.")
        request.state.environment_id = environment_id
        request.state.environment = environment
        return environment

    return _dep
