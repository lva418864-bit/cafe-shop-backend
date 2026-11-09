from http import HTTPStatus


class ApplicationError(Exception):
    def __init__(self, message: str, error_code: str, status_code: int):
        self.message = message
        self.error_code = error_code
        self.status_code = status_code
        super().__init__(message)


class Unauthorized(ApplicationError):
    def __init__(self, message: str = "Authentication is required to access this resource"):
        super().__init__(message=message, error_code="err_401", status_code=HTTPStatus.UNAUTHORIZED)


class Forbidden(ApplicationError):
    def __init__(self, message: str = "You do not have permission to perform this action"):
        super().__init__(message=message, error_code="err_403", status_code=HTTPStatus.FORBIDDEN)


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


class InvalidDateRange(ApplicationError):
    def __init__(self, message: str = "start_date must be on or before end_date"):
        super().__init__(message=message, error_code="err_400_date_range", status_code=HTTPStatus.BAD_REQUEST)


class SyncFailed(ApplicationError):
    def __init__(self, source: str, reason: str):
        super().__init__(
            message=f"Failed to sync data from {source}: {reason}",
            error_code="err_502_sync_failed",
            status_code=HTTPStatus.BAD_GATEWAY,
        )
