import math

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exception import EmailAlreadyExists, Forbidden, InvalidCredentials, UserNotFound
from app.core.security import create_access_token, hash_password, verify_password
from app.models.user import User
from app.repository import user_repository as user_repo
from app.schemas.user_schema import (
    ChangePasswordRequest,
    CreateUserRequest,
    LoginRequest,
    UpdateUserRequest,
)


class UserService:
    # ---------------------------------------------------------------- create
    def create_user(self, db: Session, request: CreateUserRequest) -> User:
        existing_email = user_repo.get_by_email(db, request.email)
        if existing_email:
            raise EmailAlreadyExists(request.email)

        user = User(
            username=request.username.strip(),
            email=request.email,
            password=hash_password(request.password),
            role="customer",
            is_active=True,
        )

        return user_repo.create(db, user)

    # ------------------------------------------------------------------ auth
    def authenticate(self, db: Session, request: LoginRequest) -> User:
        user = user_repo.get_by_email(db, request.email)
        if not user or not verify_password(request.password, user.password):
            raise InvalidCredentials()
        if not user.is_active:
            raise Forbidden("This account has been deactivated")
        return user

    def issue_token_for(self, user: User) -> tuple[str, int]:
        claims = {"sub": str(user.id), "email": user.email, "role": user.role}
        token = create_access_token(claims)
        return token, settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES

    # ------------------------------------------------------------------ read
    def get_all_user(self, db: Session, page: int = 1, page_size: int = 20):
        page = max(page, 1)
        page_size = min(max(page_size, 1), 100)
        skip = (page - 1) * page_size

        total_items = user_repo.count_all(db)
        items = user_repo.get_all(db, skip=skip, limit=page_size)
        total_pages = math.ceil(total_items / page_size) if total_items else 0

        return items, {
            "page": page,
            "page_size": page_size,
            "total_items": total_items,
            "total_pages": total_pages,
        }

    def get_user_by_id(self, db: Session, user_id: int) -> User:
        user = user_repo.get_by_id(db, user_id)
        if not user:
            raise UserNotFound(user_id)
        return user

    # ---------------------------------------------------------------- update
    def update_user(self, db: Session, user_id: int, request: UpdateUserRequest) -> User:
        user = self.get_user_by_id(db, user_id)

        if user.email != request.email:
            email_exists = user_repo.get_by_email(db, request.email)
            if email_exists:
                raise EmailAlreadyExists(request.email)

        user.username = request.username.strip()
        user.email = request.email
        user.is_active = request.is_active

        return user_repo.update(db, user)

    def update_role(self, db: Session, user_id: int, role: str) -> User:
        user = self.get_user_by_id(db, user_id)
        user.role = role
        return user_repo.update(db, user)

    def change_password(self, db: Session, user_id: int, request: ChangePasswordRequest) -> User:
        user = self.get_user_by_id(db, user_id)
        if not verify_password(request.old_password, user.password):
            raise InvalidCredentials()

        user.password = hash_password(request.new_password)
        return user_repo.update(db, user)

    # ---------------------------------------------------------------- delete
    def delete_user(self, db: Session, user_id: int) -> User:
        user = self.get_user_by_id(db, user_id)
        return user_repo.soft_delete(db, user)
