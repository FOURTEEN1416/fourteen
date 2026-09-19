"""认证：有效 JWT 可通过 verify_api_key_dep（用户侧不再需要前端打进 API Key）。"""

from __future__ import annotations

import os

import pytest
from fastapi import Depends, FastAPI, Security
from fastapi.testclient import TestClient

os.environ.setdefault("AI_GF_ENV", "dev")
os.environ.setdefault("JWT_SECRET", "test-secret-for-jwt-or-apikey-auth-32chars-ok!")


@pytest.fixture()
def client():
    from api.auth import configure_auth, verify_api_key_dep
    from api.auth_jwt import create_access_token

    configure_auth(True, "super-secret-api-key-32chars-minimum!!")
    app = FastAPI()

    @app.get("/protected")
    def protected(_ok: bool = Security(verify_api_key_dep)):
        return {"ok": True}

    @app.get("/protected2")
    def protected2(_ok: bool = Depends(verify_api_key_dep)):
        return {"ok": True}

    token = create_access_token({"sub": "2", "type": "access"})
    return TestClient(app), token


def test_jwt_passes_without_api_key(client):
    c, token = client
    r = c.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_api_key_still_works_for_machine(client):
    c, _t = client
    r = c.get("/protected", headers={"X-API-Key": "super-secret-api-key-32chars-minimum!!"})
    assert r.status_code == 200


def test_anonymous_rejected_when_api_key_enabled(client):
    c, _t = client
    r = c.get("/protected")
    assert r.status_code == 401


def test_invalid_jwt_falls_back_to_api_key_check(client):
    c, _t = client
    r = c.get("/protected", headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401
