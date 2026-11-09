"""
Orphan media file scanner and cleanup utility.

Usage (defaults to dry-run):
    python -m app.services.media_cleanup
    python -m app.services.media_cleanup --delete
"""

import argparse
import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.product import ProductImage
from app.services.image_service import _STORAGE_KEY_PATTERN

logger = logging.getLogger(__name__)


def find_or_remove_orphan_files(
    db: Session,
    upload_dir: str | None = None,
    dry_run: bool = True,
) -> list[str]:
    """
    Scan UPLOAD_DIR for image files that have no matching ProductImage row in PostgreSQL.
    Defaults to dry_run=True (only lists orphan filenames without deleting).
    """
    root = Path(upload_dir or settings.UPLOAD_DIR).resolve()
    if not root.exists():
        return []

    known_keys = {row[0] for row in db.query(ProductImage.storage_key).all()}
    orphans: list[str] = []

    for entry in sorted(root.iterdir()):
        if not entry.is_file():
            continue
        if not _STORAGE_KEY_PATTERN.match(entry.name):
            continue
        if entry.name not in known_keys:
            orphans.append(entry.name)
            if not dry_run:
                entry.unlink(missing_ok=True)
                logger.info("Deleted orphan media file: %s", entry.name)

    return orphans


def main() -> int:
    parser = argparse.ArgumentParser(
        description="List (or delete) orphan product image files in UPLOAD_DIR with no DB record."
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Actually delete orphan files (default is dry-run).",
    )
    args = parser.parse_args()
    dry_run = not args.delete

    db = SessionLocal()
    try:
        orphans = find_or_remove_orphan_files(db, upload_dir=settings.UPLOAD_DIR, dry_run=dry_run)
        mode = "DRY-RUN" if dry_run else "DELETED"
        print(f"[{mode}] Found {len(orphans)} orphan file(s) in {settings.UPLOAD_DIR}")
        for name in orphans:
            print(f"  - {name}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
