"""Session login/logout endpoints for the Firebase Authentication flow.

Client-side flow (see app/templates/sign_in.html): the browser runs
`signInWithPopup(new GoogleAuthProvider())` via the Firebase JS SDK, gets a
fresh ID token, and POSTs it here. The backend verifies it, mints a long-lived
session cookie, and sets it HttpOnly so the rest of the app (server-rendered
Jinja2 pages, no client-side token handling needed on every request) just
reads a cookie like any traditional session.
"""
import logging
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel

from app.core.firebase_auth import create_session_cookie, verify_id_token
from app.core.session_auth import SESSION_COOKIE_NAME

logger = logging.getLogger("gemini_provisioner.auth_routes")

router = APIRouter()

# Firebase session cookies: 5 minutes to 14 days. Five days balances "don't
# make people sign in constantly" against "a stolen cookie doesn't work
# forever."
_SESSION_LIFETIME = timedelta(days=5)


class SessionPayload(BaseModel):
    id_token: str


@router.post("/auth/session")
async def create_session(payload: SessionPayload, response: Response):
    try:
        verify_id_token(payload.id_token)
    except Exception as e:
        logger.info("Rejected sign-in: invalid ID token (%s)", e)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid ID token.")

    try:
        cookie = create_session_cookie(payload.id_token, expires_in=_SESSION_LIFETIME)
    except Exception as e:
        logger.error("Failed to mint session cookie: %s", e)
        raise HTTPException(status_code=500, detail="Could not create a session.")

    response.set_cookie(
        SESSION_COOKIE_NAME,
        cookie,
        max_age=int(_SESSION_LIFETIME.total_seconds()),
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    return {"success": True}


@router.post("/auth/logout")
async def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return {"success": True}
