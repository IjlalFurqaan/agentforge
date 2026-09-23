"""Password hashing, JWT access tokens, opaque refresh tokens and at-rest encryption."""

from __future__ import annotations

import base64
import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.config import Settings, get_settings

_hasher = PasswordHasher()  # argon2id with library defaults (OWASP-compliant parameters)

# A real hash of a random password, used to equalise timing when the user does not exist.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


# --- access tokens ----------------------------------------------------------------------------


class TokenError(Exception):
    pass


@dataclass(frozen=True)
class AccessTokenClaims:
    subject: uuid.UUID
    role: str
    jti: str
    expires_at: datetime


def create_access_token(
    user_id: uuid.UUID, role: str, settings: Settings | None = None
) -> tuple[str, int]:
    settings = settings or get_settings()
    now = datetime.now(UTC)
    ttl = timedelta(minutes=settings.access_token_ttl_minutes)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "role": role,
        "type": "access",
        "iat": now,
        "nbf": now,
        "exp": now + ttl,
        "jti": secrets.token_hex(8),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    }
    token = jwt.encode(payload, settings.secret_key, algorithm="HS256")
    return token, int(ttl.total_seconds())


def decode_access_token(token: str, settings: Settings | None = None) -> AccessTokenClaims:
    settings = settings or get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=["HS256"],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "sub", "aud", "iss"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    if payload.get("type") != "access":
        raise TokenError("wrong token type")
    try:
        subject = uuid.UUID(payload["sub"])
    except (ValueError, KeyError) as exc:
        raise TokenError("invalid subject") from exc
    return AccessTokenClaims(
        subject=subject,
        role=str(payload.get("role", "viewer")),
        jti=str(payload.get("jti", "")),
        expires_at=datetime.fromtimestamp(payload["exp"], UTC),
    )


# --- refresh tokens ---------------------------------------------------------------------------


def new_refresh_token() -> str:
    """Opaque, high-entropy token. Only its SHA-256 digest is stored."""
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# --- short-lived signed state (OAuth `state` parameter) ---------------------------------------


def create_signed_state(data: dict[str, Any], ttl_seconds: int = 600) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {**data, "type": "oauth_state", "iat": now, "exp": now + timedelta(seconds=ttl_seconds),
               "nonce": secrets.token_hex(8)}
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def verify_signed_state(state: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        payload = jwt.decode(state, settings.secret_key, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    if payload.get("type") != "oauth_state":
        raise TokenError("wrong token type")
    return payload


# --- at-rest encryption -----------------------------------------------------------------------


def _fernet(settings: Settings | None = None) -> Fernet:
    settings = settings or get_settings()
    if settings.encryption_key:
        return Fernet(settings.encryption_key.encode())
    derived = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=b"agentforge-fernet", info=b"github-tokens"
    ).derive(settings.secret_key.encode())
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise TokenError("cannot decrypt stored secret (encryption key changed?)") from exc


def redact(text: str, secrets_to_hide: list[str]) -> str:
    for s in secrets_to_hide:
        if s:
            text = text.replace(s, "***REDACTED***")
    return text
