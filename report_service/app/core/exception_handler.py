import logging
from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.exception import ApplicationError

logger = logging.getLogger(__name__)


async def application_error_handler(request: Request, exception: ApplicationError):
    if exception.status_code >= 500:
        logger.error(
            "Application error on %s %s: [%s] %s (HTTP %s)",
            request.method,
            request.url.path,
            exception.error_code,
            exception.message,
            exception.status_code,
        )
    else:
        logger.warning(
            "Client error on %s %s: [%s] %s (HTTP %s)",
            request.method,
            request.url.path,
            exception.error_code,
            exception.message,
            exception.status_code,
        )
    return JSONResponse(
        status_code=exception.status_code,
        content={
            "success": False,
            "error_code": exception.error_code,
            "message": exception.message,
            "data": None,
        },
    )
