"""
Container start-up tasks, run by ``entrypoint.sh`` before uvicorn starts.

1. Load and validate settings. Validation errors are printed as
   "FIELD: message" only - never with input values, so secrets do not
   end up in the logs.
2. Wait for PostgreSQL with a limited number of retries (Railway has no
   ``depends_on``, so the database may still be starting).
3. Under a PostgreSQL advisory lock (safe when two deploys overlap), create
   missing tables. ``create_all`` only creates what does not exist yet, so
   this is idempotent.

Tuning (optional env vars): DB_CONNECT_MAX_ATTEMPTS (default 30),
DB_CONNECT_RETRY_SECONDS (default 2).
"""

import logging
import os
import sys
import time

from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

SERVICE_NAME = "user_service"
ADVISORY_LOCK_ID = 7301001

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
    from app.core.database import Base
    from app.models import user  # noqa: F401  (registers models on Base.metadata)

    with engine.connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": ADVISORY_LOCK_ID})
        try:
            Base.metadata.create_all(bind=conn)
            conn.commit()
            log.info("Schema ready (create_all is idempotent).")
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": ADVISORY_LOCK_ID})
            conn.commit()


def main() -> None:
    settings = _load_settings()
    engine = _wait_for_database(settings.database_url)
    _prepare_schema(engine)
    engine.dispose()
    log.info("Prestart finished.")


if __name__ == "__main__":
    main()
