from datetime import datetime, timedelta, timezone
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


def create_service_token() -> str:
    """
    report_service holds the same shared JWT_SECRET_KEY as user_service and
    operation_service, so it can mint its own short-lived "service account"
    token to call their APIs during a sync run. This is NOT a user token -
    it never leaves report_service and is only ever used in the
    Authorization header of the outbound sync HTTP calls.
    """
    now = datetime.now(timezone.utc)
    claims = {
        "sub": "report_service",
        "email": "sync@report-service.internal",
        "role": "admin",  # needs admin/staff to list all users/orders during sync
        "iat": now,
        "exp": now + timedelta(minutes=settings.SERVICE_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(claims, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
