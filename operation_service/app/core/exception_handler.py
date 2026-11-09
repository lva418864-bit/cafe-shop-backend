from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.exception import ApplicationError


async def application_error_handler(request: Request, exception: ApplicationError):
    return JSONResponse(
        status_code=exception.status_code,
        content={
            "success": False,
            "error_code": exception.error_code,
            "message": exception.message,
            "data": None,
        },
    )
