from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.dependencies.db import get_db
from app.services.image_service import ProductImageService

router = APIRouter(prefix="/media", tags=["media"])
image_service = ProductImageService()


@router.get("/{storage_key:path}")
def serve_media_file(
    storage_key: str,
    db: Session = Depends(get_db),
):
    """Public read endpoint for uploaded product images with strict nosniff and immutable caching."""
    file_path, content_type = image_service.get_media_file(db, storage_key)
    return FileResponse(
        path=file_path,
        media_type=content_type,
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )
