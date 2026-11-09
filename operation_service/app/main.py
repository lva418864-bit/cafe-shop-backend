from contextlib import asynccontextmanager
import logging
import threading
import time

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import Base, SessionLocal, engine
from app.dependencies.db import get_db

# Import models so SQLAlchemy registers them on Base.metadata before create_all().
from app.models.category import Category  # noqa: F401
from app.models.customization import CustomizationGroup, CustomizationOption  # noqa: F401
from app.models.product import Product, ProductImage  # noqa: F401
from app.models.order import Order, OrderItem  # noqa: F401
from app.models.payment import (  # noqa: F401
    IdempotencyRecord,
    Payment,
    PaymentAttempt,
    PaymentEvent,
    Refund,
    VerificationJob,
)
from app.models.stock_movement import StockMovement  # noqa: F401

from app.api.product_route import image_router as product_image_router, router as product_router
from app.api.media_route import router as media_router
from app.api.order_route import router as order_router
from app.api.payment_route import router as payment_router
from app.api.category_route import router as category_router

from app.core.exception import ApplicationError
from app.core.exception_handler import application_error_handler
from app.services.bakong_khqr import PaymentVerificationWorker

logger = logging.getLogger(__name__)

# NOTE: create_all() is convenient for local development; Alembic migrations
# are provided in operation_service/migrations/ for production PostgreSQL.
Base.metadata.create_all(bind=engine)


def _background_verification_loop(stop_event: threading.Event) -> None:
    """Poll queued Bakong verification jobs and reconcile expired QR attempts."""
    worker = PaymentVerificationWorker()
    last_reconcile = 0.0
    while not stop_event.is_set():
        db = SessionLocal()
        try:
            worker.process_due_jobs(db, batch_size=10)
            now_mono = time.monotonic()
            if now_mono - last_reconcile >= 60.0:
                worker.reconcile_expired_payments(db)
                last_reconcile = now_mono
        except Exception as exc:
            logger.warning("Background verification worker iteration error: %s", exc)
        finally:
            db.close()
        stop_event.wait(3.0)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    stop_event = threading.Event()
    worker_thread: threading.Thread | None = None
    if settings.ENVIRONMENT != "test":
        worker_thread = threading.Thread(
            target=_background_verification_loop,
            args=(stop_event,),
            name="bakong-verification-worker",
            daemon=True,
        )
        worker_thread.start()
    try:
        yield
    finally:
        stop_event.set()
        if worker_thread is not None:
            worker_thread.join(timeout=5.0)


app = FastAPI(
    title="Coffee Shop - Operation & Bakong KHQR Payment Service",
    lifespan=lifespan,
)

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
            "service": "operation_service",
            "database": "connected",
        }
    except Exception as e:
        logger.error("Health check database probe failed: %s", e)
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "service": "operation_service",
                "database": "disconnected",
            },
        )


app.include_router(category_router)
app.include_router(product_router)
# Alias prefixes so both Kong's strip_path (/product) and regex route (/api/products/{id}/images) resolve cleanly
app.include_router(product_image_router, prefix="/products", include_in_schema=False)
app.include_router(product_image_router, prefix="/api/products", include_in_schema=False)
app.include_router(media_router)
app.include_router(media_router, prefix="/api", include_in_schema=False)
app.include_router(order_router)
app.include_router(payment_router)

app.add_exception_handler(ApplicationError, application_error_handler)


@app.get("/")
async def root():
    return {"message": "Coffee Shop Operation Service is running"}
