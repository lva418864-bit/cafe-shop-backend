"""
Security primitives for the auth (user) service.

Responsibilities:
- Hash & verify user passwords with bcrypt (no more plain-text storage).
- Issue and verify signed JWT access tokens.

The JWT_SECRET_KEY / JWT_ALGORITHM must be identical across user_service,
operation_service and report_service .env files: user_service is the only
service that *issues* tokens (via /user/login), while the other services
only *verify* them locally (stateless auth, no network call needed).
"""

from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt

from app.core.config import settings
from app.core.exception import InvalidToken, TokenExpired

_BCRYPT_MAX_BYTES = 72  # bcrypt silently truncates beyond 72 bytes; guard explicitly.


def hash_password(plain_password: str) -> str:
    password_bytes = plain_password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    hashed = bcrypt.hashpw(password_bytes, bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        password_bytes = plain_password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
        return bcrypt.checkpw(password_bytes, hashed_password.encode("utf-8"))
    except (ValueError, TypeError):
        # Covers malformed/legacy (plain-text) hashes already in the DB.
        return False


def create_access_token(claims: dict[str, Any], expires_minutes: int | None = None) -> str:
    to_encode = claims.copy()
    now = datetime.now(timezone.utc)
    expire = now + timedelta(
        minutes=expires_minutes if expires_minutes is not None else settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES
    )
    to_encode.update({"iat": now, "exp": expire})
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise TokenExpired()
    except jwt.InvalidTokenError:
        raise InvalidToken()
