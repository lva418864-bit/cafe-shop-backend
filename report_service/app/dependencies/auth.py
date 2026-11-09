from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.core.exception import Forbidden, Unauthorized
from app.core.security import decode_access_token

_bearer_scheme = HTTPBearer(auto_error=False)


class CurrentUser(BaseModel):
    id: int
    email: str
    role: str


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> CurrentUser:
    if credentials is None or not credentials.credentials:
        raise Unauthorized("Missing bearer token")

    payload = decode_access_token(credentials.credentials)
    raw_sub = payload.get("sub")
    email = payload.get("email")
    role = payload.get("role")
    if raw_sub is None or role is None:
        raise Unauthorized("Token payload is missing required claims")

    user_id = int(raw_sub) if str(raw_sub).isdigit() else -1
    return CurrentUser(id=user_id, email=email or "", role=role)


def require_roles(*allowed_roles: str):
    """Reports expose business/financial data, so every report endpoint is staff/admin-only."""

    def _checker(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if current_user.role not in allowed_roles:
            raise Forbidden(f"This action requires one of the roles: {', '.join(allowed_roles)}")
        return current_user

    return _checker
