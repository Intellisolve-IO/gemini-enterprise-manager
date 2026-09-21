import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient

from app.main import app
from app.core.session_auth import SESSION_COOKIE_NAME

client = TestClient(app)


def test_create_session_sets_cookie_on_valid_token():
    with patch("app.auth_routes.verify_id_token", return_value={"uid": "u1", "email": "a@b.com"}), \
         patch("app.auth_routes.create_session_cookie", return_value="minted-cookie-value"):
        r = client.post("/auth/session", json={"id_token": "valid-token"})
    assert r.status_code == 200
    assert r.json()["success"] is True
    assert client.cookies.get(SESSION_COOKIE_NAME) == "minted-cookie-value"


def test_create_session_rejects_invalid_token():
    with patch("app.auth_routes.verify_id_token", side_effect=Exception("bad token")):
        r = client.post("/auth/session", json={"id_token": "garbage"})
    assert r.status_code == 401


def test_create_session_500_when_cookie_minting_fails():
    with patch("app.auth_routes.verify_id_token", return_value={"uid": "u1"}), \
         patch("app.auth_routes.create_session_cookie", side_effect=RuntimeError("boom")):
        r = client.post("/auth/session", json={"id_token": "valid-token"})
    assert r.status_code == 500


def test_logout_clears_cookie():
    client.cookies.set(SESSION_COOKIE_NAME, "something")
    r = client.post("/auth/logout")
    assert r.status_code == 200
    assert r.json()["success"] is True
