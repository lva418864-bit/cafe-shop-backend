import logging

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import Base, engine
from app.dependencies.db import get_db

# Import models so SQLAlchemy registers them on Base.metadata before create_all().
from app.models.snapshot import OrderItemSnapshot, OrderSnapshot, ProductSnapshot, UserSnapshot  # noqa: F401

from app.api.report_route import router as report_router

from app.core.exception import ApplicationError
from app.core.exception_handler import application_error_handler

logger = logging.getLogger(__name__)

# Ensure snapshot tables exist
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Coffee Shop - Report Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["health"])
def health_check(db: Session = Depends(get_db)):
    """Health check verifying PostgreSQL connectivity and service status."""
    try:
        db.execute(text("SELECT 1"))
        return {
            "status": "ok",
            "service": "report_service",
            "database": "connected",
        }
    except Exception as e:
        logger.error("Health check database probe failed: %s", e)
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "service": "report_service",
                "database": "disconnected",
            },
        )


app.include_router(report_router)

app.add_exception_handler(ApplicationError, application_error_handler)


@app.get("/")
async def root():
    return {"message": "Coffee Shop Report Service is running"}
