"""
operation_service never issues JWTs - only user_service does (at /user/login).
This module simply verifies the signature/expiry of tokens issued elsewhere,
using the shared JWT_SECRET_KEY / JWT_ALGORITHM. This keeps services loosely
coupled: no network call back to user_service is needed on every request.
"""

from typing import Any

import jwt

from app.core.config import settings
from app.core.exception import InvalidToken, TokenExpired


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise TokenExpired()
    except jwt.InvalidTokenError:
        raise InvalidToken()
