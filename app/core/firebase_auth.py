"""Thin wrapper around the Firebase Admin SDK: app initialization + the three
calls the session-cookie auth flow needs (verify an ID token, mint a session
cookie from it, verify a session cookie on later requests).

Firebase project setup (enabling Identity Platform / Google sign-in) is a
one-time console/CLI step, not something this module does - see
setup_instructions.md. On Cloud Run, `firebase_admin.initialize_app()` with no
arguments uses Application Default Credentials, the same ambient identity the
rest of this app already relies on (google.auth.default() elsewhere) - no
separate Firebase service account key is needed.
"""
import logging
from datetime import timedelta
from typing import Any, Dict

import firebase_admin
from firebase_admin import auth as fb_auth

logger = logging.getLogger("gemini_provisioner.firebase_auth")

_app: firebase_admin.App | None = None


def _ensure_app() -> firebase_admin.App:
    global _app
    if _app is None:
        _app = firebase_admin.initialize_app()
        logger.info("Initialized Firebase Admin app for project %s", _app.project_id)
    return _app


def verify_id_token(id_token: str) -> Dict[str, Any]:
    """Verify a client-obtained Firebase ID token (from the sign-in popup).
    Raises on an invalid/expired token."""
    _ensure_app()
    return fb_auth.verify_id_token(id_token, app=_app)


def create_session_cookie(id_token: str, expires_in: timedelta) -> str:
    """Mint a long-lived session cookie from a freshly-verified ID token.
    `expires_in` must be between 5 minutes and 14 days (Firebase's limits)."""
    _ensure_app()
    return fb_auth.create_session_cookie(id_token, expires_in=expires_in, app=_app)


def verify_session_cookie(session_cookie: str, check_revoked: bool = False) -> Dict[str, Any]:
    """Verify the session cookie set on every authenticated request. Revocation
    checks cost a network round-trip; leave `check_revoked=False` for normal
    traffic and only set it True around sensitive actions (e.g. membership/role
    changes) - see app/core/session_auth.py."""
    _ensure_app()
    return fb_auth.verify_session_cookie(session_cookie, check_revoked=check_revoked, app=_app)


def get_user_by_email(email: str) -> Dict[str, Any]:
    """Look up a Firebase user by email (e.g. to seed tenant membership for
    someone who hasn't signed in yet). Raises firebase_admin.auth.UserNotFoundError
    if they've never signed in."""
    _ensure_app()
    user = fb_auth.get_user_by_email(email, app=_app)
    return {"uid": user.uid, "email": user.email}
