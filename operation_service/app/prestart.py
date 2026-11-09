"""
Container start-up tasks, run by ``entrypoint.sh`` before uvicorn starts.

1. Load and validate settings (JWT, DB and Bakong variables). Validation
   errors are printed as "FIELD: message" only - never with input values,
   so secrets do not end up in the logs.
2. Check that UPLOAD_DIR exists and is writable by the container user
   (Railway volumes are mounted as root; see docs/RAILWAY_DEPLOY_GUIDE.md).
3. Wait for PostgreSQL with a limited number of retries (Railway has no
   ``depends_on``, so the database may still be starting).
4. Under a PostgreSQL advisory lock (safe when two deploys overlap):
   - ``create_all`` creates missing base tables (orders, products, ...),
     which the Alembic revisions expect to exist;
   - ``alembic upgrade head`` applies the idempotent Bakong / product-image
     revisions and records the version in ``alembic_version``.

Tuning (optional env vars): DB_CONNECT_MAX_ATTEMPTS (default 30),
DB_CONNECT_RETRY_SECONDS (default 2).
"""

import logging
import os
import sys
import tempfile
import time
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

SERVICE_NAME = "operation_service"
ADVISORY_LOCK_ID = 7301002
SERVICE_DIR = Path(__file__).resolve().parent.parent

logging.basicConfig(level=logging.INFO, format="%(asctime)s [prestart:" + SERVICE_NAME + "] %(levelname)s %(message)s")
log = logging.getLogger("prestart")


def _load_settings():
    try:
        from app.core.config import settings
    except ValidationError as exc:
        log.error("Invalid configuration (values are not shown):")
        for err in exc.errors():
            field = ".".join(str(part) for part in err.get("loc", ())) or "settings"
            log.error("  - %s: %s", field, err.get("msg", "invalid value"))
        log.error("Set the missing/invalid variables in the service Variables tab (or .env) and redeploy.")
        sys.exit(1)
    return settings


def _check_upload_dir(upload_dir: str) -> None:
    path = Path(upload_dir)
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path, prefix=".write-test-", delete=True):
            pass
    except OSError as exc:
        log.error(
            "UPLOAD_DIR=%s is not writable by uid=%s (%s). On Railway, volumes are mounted as root: "
            "set the service variable RAILWAY_RUN_UID=0 (see docs/RAILWAY_DEPLOY_GUIDE.md).",
            upload_dir,
            os.getuid() if hasattr(os, "getuid") else "?",
            exc.__class__.__name__,
        )
        sys.exit(1)
    log.info("UPLOAD_DIR=%s is writable.", upload_dir)


def _wait_for_database(database_url: str):
    attempts = int(os.environ.get("DB_CONNECT_MAX_ATTEMPTS", "30"))
    delay = float(os.environ.get("DB_CONNECT_RETRY_SECONDS", "2"))
    engine = create_engine(database_url, pool_pre_ping=True, connect_args={"connect_timeout": 5})
    target = f"host={engine.url.host} port={engine.url.port} db={engine.url.database} user={engine.url.username}"
    for attempt in range(1, attempts + 1):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            log.info("Database reachable (%s) after %d attempt(s).", target, attempt)
            return engine
        except OperationalError as exc:
            reason = str(exc.orig).strip().splitlines()[0] if exc.orig else exc.__class__.__name__
            log.warning("Database not reachable yet (attempt %d/%d, %s): %s", attempt, attempts, target, reason)
            time.sleep(delay)
    log.error("Giving up: database not reachable after %d attempts (%s).", attempts, target)
    sys.exit(1)


def _prepare_schema(engine) -> None:
    from alembic import command
    from alembic.config import Config

    from app.core.database import Base
    from app.models import category, customization, order, payment, product, stock_movement  # noqa: F401

    with engine.connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": ADVISORY_LOCK_ID})
        try:
            Base.metadata.create_all(bind=conn)
            conn.commit()
            log.info("Base tables ready (create_all is idempotent). Running 'alembic upgrade head'...")
            command.upgrade(Config(str(SERVICE_DIR / "alembic.ini")), "head")
            # alembic's fileConfig() disables loggers created before it ran.
            log.disabled = False
            log.info("Alembic upgrade head finished.")
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": ADVISORY_LOCK_ID})
            conn.commit()


def main() -> None:
    settings = _load_settings()
    _check_upload_dir(settings.UPLOAD_DIR)
    engine = _wait_for_database(settings.database_url)
    _prepare_schema(engine)
    engine.dispose()
    log.info("Prestart finished.")


if __name__ == "__main__":
    main()
