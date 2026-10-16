from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core import security
from tests.conftest import register_and_login

PASSWORD = "Sup3r-Secret-Pass!"


def test_password_hash_roundtrip():
    h = security.hash_password(PASSWORD)
    assert h.startswith("$argon2id$")
    assert security.verify_password(PASSWORD, h)
    assert not security.verify_password("wrong", h)
    assert not security.verify_password(PASSWORD, None)


def test_access_token_roundtrip_and_validation(settings):
    import uuid

    uid = uuid.uuid4()
    token, ttl = security.create_access_token(uid, "engineer", settings)
    claims = security.decode_access_token(token, settings)
    assert claims.subject == uid and claims.role == "engineer" and ttl == 900

    expired = jwt.encode({"sub": str(uid), "type": "access", "iat": datetime.now(UTC) - timedelta(hours=2),
                          "exp": datetime.now(UTC) - timedelta(hours=1), "aud": settings.jwt_audience,
                          "iss": settings.jwt_issuer}, settings.secret_key, algorithm="HS256")
    with pytest.raises(security.TokenError):
        security.decode_access_token(expired, settings)
    wrong_aud = jwt.encode({"sub": str(uid), "type": "access", "iat": datetime.now(UTC),
                            "exp": datetime.now(UTC) + timedelta(minutes=5), "aud": "other", "iss": settings.jwt_issuer},
                           settings.secret_key, algorithm="HS256")
    with pytest.raises(security.TokenError):
        security.decode_access_token(wrong_aud, settings)
    none_alg = jwt.encode({"sub": str(uid), "type": "access"}, key="", algorithm="none")
    with pytest.raises(security.TokenError):
        security.decode_access_token(none_alg, settings)


def test_secret_encryption_and_state(settings):
    blob = security.encrypt_secret("gho_example")
    assert "gho_example" not in blob
    assert security.decrypt_secret(blob) == "gho_example"
    state = security.create_signed_state({"uid": "u1"})
    assert security.verify_signed_state(state)["uid"] == "u1"
    with pytest.raises(security.TokenError):
        security.verify_signed_state(state + "x")


async def test_register_login_me(client):
    r = await client.post("/api/auth/register", json={"email": "Admin@Example.com", "password": PASSWORD})
    assert r.status_code == 201
    assert r.json()["role"] == "admin"  # first user
    r = await client.post("/api/auth/register", json={"email": "dev@example.com", "password": PASSWORD})
    assert r.json()["role"] == "engineer"
    r = await client.post("/api/auth/register", json={"email": "dev@example.com", "password": PASSWORD})
    assert r.status_code == 409

    r = await client.post("/api/auth/login", json={"email": "admin@example.com", "password": PASSWORD})
    assert r.status_code == 200
    body = r.json()
    assert "af_refresh" in r.headers["set-cookie"] and "HttpOnly" in r.headers["set-cookie"]
    me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.json()["email"] == "admin@example.com"


async def test_weak_password_rejected(client):
    r = await client.post("/api/auth/register", json={"email": "a@example.com", "password": "alllowercaseletters"})
    assert r.status_code == 422


async def test_invalid_login_generic_error(client):
    await register_and_login(client)
    r1 = await client.post("/api/auth/login", json={"email": "admin@example.com", "password": "nope"})
    r2 = await client.post("/api/auth/login", json={"email": "ghost@example.com", "password": "nope"})
    assert r1.status_code == r2.status_code == 401
    assert r1.json()["error"]["message"] == r2.json()["error"]["message"]


async def test_login_rate_limited(client):
    await register_and_login(client)
    codes = [(await client.post("/api/auth/login", json={"email": "admin@example.com", "password": "bad"})).status_code
             for _ in range(7)]
    assert codes[:5] == [401] * 5 and codes[-1] == 429


async def test_refresh_rotation_and_reuse_detection(client):
    await register_and_login(client)
    first_cookie = client.cookies.get("af_refresh")
    r = await client.post("/api/auth/refresh")
    assert r.status_code == 200
    second_cookie = client.cookies.get("af_refresh")
    assert second_cookie and second_cookie != first_cookie

    # Replaying the rotated token is treated as theft: the whole family is revoked.
    client.cookies.set("af_refresh", first_cookie, path="/api/auth")
    r = await client.post("/api/auth/refresh")
    assert r.status_code == 401 and "reuse" in r.json()["error"]["message"]
    client.cookies.set("af_refresh", second_cookie, path="/api/auth")
    r = await client.post("/api/auth/refresh")
    assert r.status_code == 401


async def test_logout_revokes_refresh_token(client):
    await register_and_login(client)
    cookie = client.cookies.get("af_refresh")
    assert (await client.post("/api/auth/logout")).status_code == 204
    client.cookies.set("af_refresh", cookie, path="/api/auth")
    assert (await client.post("/api/auth/refresh")).status_code == 401


async def test_rbac(client):
    admin = await register_and_login(client)
    viewer_token = await register_and_login(client, "viewer@example.com")
    users = (await client.get("/api/auth/users", headers={"Authorization": f"Bearer {admin}"})).json()
    viewer_id = next(u["id"] for u in users if u["email"] == "viewer@example.com")
    r = await client.patch(f"/api/auth/users/{viewer_id}/role", json={"role": "viewer"},
                           headers={"Authorization": f"Bearer {admin}"})
    assert r.json()["role"] == "viewer"
    # role change applies immediately, even with an already-issued token
    r = await client.post("/api/repositories", json={"source": "local", "locator": "/tmp/x"},
                          headers={"Authorization": f"Bearer {viewer_token}"})
    assert r.status_code == 403
    r = await client.get("/api/auth/users", headers={"Authorization": f"Bearer {viewer_token}"})
    assert r.status_code == 403
    assert (await client.get("/api/auth/me")).status_code == 401
