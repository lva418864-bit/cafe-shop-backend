from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.exception import Forbidden, InactiveUser, Unauthorized
from app.core.security import decode_access_token
from app.dependencies.db import get_db
from app.models.user import User
from app.repository import user_repository as user_repo

# auto_error=False so we can raise our own ApplicationError (consistent
# JSON envelope) instead of FastAPI's default 403 "Not authenticated" body.
_bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    db: Session = Depends(get_db),
) -> User:
    if credentials is None or not credentials.credentials:
        raise Unauthorized("Missing bearer token")

    payload = decode_access_token(credentials.credentials)
    raw_sub = payload.get("sub")
    if raw_sub is None:
        raise Unauthorized("Token payload is missing the subject claim")

    # Internal service-account tokens (e.g. report_service's sync job, see
    # that service's core/security.create_service_token) have a non-numeric
    # "sub" like "report_service" and no corresponding row in `users`. They
    # are trusted directly off the signed claims instead of via a DB lookup.
    if not str(raw_sub).isdigit():
        role = payload.get("role")
        if role is None:
            raise Unauthorized("Service token payload is missing the role claim")
        return User(
            id=-1,
            username=str(raw_sub),
            email=payload.get("email") or "",
            role=role,
            is_active=True,
        )

    user = user_repo.get_by_id(db, int(raw_sub))
    if user is None:
        raise Unauthorized("User attached to this token no longer exists")
    if not user.is_active:
        raise InactiveUser()

    return user


def require_roles(*allowed_roles: str):
    """Dependency factory: require the current user to hold one of the given roles."""

    def _checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed_roles:
            raise Forbidden(f"This action requires one of the roles: {', '.join(allowed_roles)}")
        return current_user

    return _checker
