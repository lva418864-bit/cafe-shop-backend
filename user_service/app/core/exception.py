from http import HTTPStatus


class ApplicationError(Exception):
    def __init__(self, message: str, error_code: str, status_code: int):
        self.message = message
        self.error_code = error_code
        self.status_code = status_code

        super().__init__(message)


class UserNotFound(ApplicationError):
    def __init__(self, user_id: int):
        super().__init__(
            message=f"User with id {user_id} not found", error_code="err_404", status_code=HTTPStatus.NOT_FOUND
        )


class EmailAlreadyExists(ApplicationError):
    def __init__(self, email: str):
        super().__init__(message=f"Email {email} already exists", error_code="err_409", status_code=HTTPStatus.CONFLICT)


class Unauthorized(ApplicationError):
    def __init__(self, message: str = "Authentication is required to access this resource"):
        super().__init__(
            message=message,
            error_code="err_401",
            status_code=HTTPStatus.UNAUTHORIZED,
        )


class Forbidden(ApplicationError):
    def __init__(self, message: str = "You do not have permission to perform this action"):
        super().__init__(
            message=message,
            error_code="err_403",
            status_code=HTTPStatus.FORBIDDEN,
        )


class InvalidCredentials(ApplicationError):
    def __init__(self):
        super().__init__(
            message="Invalid email or password",
            error_code="err_401_invalid_credentials",
            status_code=HTTPStatus.UNAUTHORIZED,
        )


class InvalidToken(ApplicationError):
    def __init__(self):
        super().__init__(
            message="Invalid authentication token",
            error_code="err_401_invalid_token",
            status_code=HTTPStatus.UNAUTHORIZED,
        )


class TokenExpired(ApplicationError):
    def __init__(self):
        super().__init__(
            message="Authentication token has expired",
            error_code="err_401_token_expired",
            status_code=HTTPStatus.UNAUTHORIZED,
        )


class InactiveUser(ApplicationError):
    def __init__(self):
        super().__init__(
            message="This user account has been deactivated",
            error_code="err_403_inactive_user",
            status_code=HTTPStatus.FORBIDDEN,
        )
