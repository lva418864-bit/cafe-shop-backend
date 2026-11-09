from io import BytesIO
import logging
import os
from pathlib import Path
import re
import uuid
import warnings

from fastapi import UploadFile
from PIL import Image, UnidentifiedImageError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.exception import (
    ImagePayloadTooLarge,
    InvalidImageFile,
    ProductImageNotFound,
    ProductNotFound,
    UnsupportedImageType,
)
from app.models.product import Product, ProductImage
from app.repository import product_repository as product_repo
from app.schemas.product_schema import UpdateProductImageRequest

logger = logging.getLogger(__name__)

_STORAGE_KEY_PATTERN = re.compile(r"^[a-f0-9]{32}\.(jpg|png|webp)$")
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]")
_CHUNK_SIZE = 64 * 1024  # 64 KB chunks


def detect_magic_mime(data: bytes) -> tuple[str, str, str] | None:
    """Inspect magic bytes and return (content_type, file_extension, pillow_format)."""
    if len(data) >= 3 and data[:3] == b"\xff\xd8\xff":
        return ("image/jpeg", ".jpg", "JPEG")
    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return ("image/png", ".png", "PNG")
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ("image/webp", ".webp", "WEBP")
    return None


def sanitize_original_filename(filename: str | None) -> str | None:
    """Strip path components and unsafe characters from user-supplied filename."""
    if not filename:
        return None
    basename = filename.replace("\\", "/").split("/")[-1].strip()
    cleaned = _SAFE_FILENAME_RE.sub("_", basename).lstrip(".")
    if not cleaned:
        return None
    return cleaned[:120]


def resolve_safe_storage_path(storage_key: str, upload_dir: str | None = None) -> Path:
    """Validate storage_key format and ensure resolved path stays strictly inside UPLOAD_DIR."""
    if not _STORAGE_KEY_PATTERN.match(storage_key):
        raise ProductImageNotFound(storage_key)
    root = Path(upload_dir or settings.UPLOAD_DIR).resolve()
    target = (root / storage_key).resolve()
    if target.parent != root or not target.is_relative_to(root):
        raise ProductImageNotFound(storage_key)
    return target


async def read_upload_stream_bounded(upload_file: UploadFile, max_bytes: int | None = None) -> bytes:
    """Read upload stream in bounded chunks, raising 413 immediately if max_bytes is exceeded."""
    limit = max_bytes if max_bytes is not None else settings.UPLOAD_MAX_BYTES
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload_file.read(_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise ImagePayloadTooLarge(limit)
        chunks.append(chunk)

    if total == 0:
        raise InvalidImageFile("Uploaded image file is empty")
    return b"".join(chunks)


def validate_and_reencode_image(raw_bytes: bytes) -> tuple[bytes, str, str, int, int]:
    """
    Validate magic bytes, verify image structure & dimensions with Pillow,
    and re-encode to strip all EXIF metadata and ancillary chunks.
    Returns (clean_bytes, content_type, extension, width, height).
    """
    detected = detect_magic_mime(raw_bytes)
    if detected is None:
        raise UnsupportedImageType(settings.UPLOAD_ALLOWED_TYPES)

    content_type, ext, expected_format = detected
    if content_type not in settings.UPLOAD_ALLOWED_TYPES:
        raise UnsupportedImageType(settings.UPLOAD_ALLOWED_TYPES)

    max_dim = settings.UPLOAD_MAX_DIMENSION
    max_pixels = min(settings.UPLOAD_MAX_PIXELS, max_dim * max_dim)

    previous_max_pixels = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = max_pixels
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            try:
                with Image.open(BytesIO(raw_bytes)) as probe:
                    actual_format = (probe.format or "").upper()
                    if actual_format != expected_format:
                        raise InvalidImageFile("Image container format does not match file header")
                    width, height = probe.size
                    if width <= 0 or height <= 0:
                        raise InvalidImageFile("Image dimensions must be positive")
                    if width > max_dim or height > max_dim or (width * height) > max_pixels:
                        raise InvalidImageFile(
                            f"Image dimensions ({width}x{height}) exceed maximum allowed {max_dim}x{max_dim}"
                        )
                    probe.verify()

                with Image.open(BytesIO(raw_bytes)) as img:
                    img.load()
                    width, height = img.size
                    if width > max_dim or height > max_dim or (width * height) > max_pixels:
                        raise InvalidImageFile(
                            f"Image dimensions ({width}x{height}) exceed maximum allowed {max_dim}x{max_dim}"
                        )

                    out_buffer = BytesIO()
                    if expected_format == "JPEG":
                        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                            rgba = img.convert("RGBA")
                            background = Image.new("RGB", rgba.size, (255, 255, 255))
                            background.paste(rgba, mask=rgba.split()[3])
                            clean_img = Image.frombytes("RGB", background.size, background.tobytes())
                        else:
                            rgb = img.convert("RGB")
                            clean_img = Image.frombytes("RGB", rgb.size, rgb.tobytes())
                        clean_img.save(out_buffer, format="JPEG", quality=88, optimize=True)
                    elif expected_format == "PNG":
                        has_alpha = img.mode in ("RGBA", "LA") or (
                            img.mode == "P" and "transparency" in img.info
                        )
                        target_mode = "RGBA" if has_alpha else "RGB"
                        converted = img.convert(target_mode)
                        clean_img = Image.frombytes(target_mode, converted.size, converted.tobytes())
                        clean_img.save(out_buffer, format="PNG", optimize=True)
                    else:  # WEBP
                        has_alpha = img.mode in ("RGBA", "LA") or (
                            img.mode == "P" and "transparency" in img.info
                        )
                        target_mode = "RGBA" if has_alpha else "RGB"
                        converted = img.convert(target_mode)
                        clean_img = Image.frombytes(target_mode, converted.size, converted.tobytes())
                        clean_img.save(out_buffer, format="WEBP", quality=88)

                    clean_bytes = out_buffer.getvalue()
                    return clean_bytes, content_type, ext, width, height

            except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
                raise InvalidImageFile(
                    f"Image exceeds maximum safe pixel count ({max_pixels} pixels)"
                ) from exc
            except InvalidImageFile:
                raise
            except (UnidentifiedImageError, OSError, ValueError, SyntaxError, TypeError) as exc:
                raise InvalidImageFile("Corrupt or invalid image file") from exc
    finally:
        Image.MAX_IMAGE_PIXELS = previous_max_pixels


def write_file_atomically(clean_bytes: bytes, ext: str, upload_dir: str | None = None) -> tuple[str, Path]:
    """Write image bytes atomically via temp file + os.replace inside UPLOAD_DIR."""
    root = Path(upload_dir or settings.UPLOAD_DIR).resolve()
    root.mkdir(parents=True, exist_ok=True)

    storage_key = f"{uuid.uuid4().hex}{ext}"
    target_path = (root / storage_key).resolve()
    if target_path.parent != root or not target_path.is_relative_to(root):
        raise InvalidImageFile("Invalid storage destination")

    tmp_path = (root / f".tmp-{uuid.uuid4().hex}").resolve()
    try:
        with open(tmp_path, "wb") as f:
            f.write(clean_bytes)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, target_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        target_path.unlink(missing_ok=True)
        raise

    return storage_key, target_path


def _process_and_write_sync(raw_bytes: bytes, upload_dir: str | None = None) -> tuple[str, Path, str, int, int, int]:
    """CPU and disk work executed inside worker threadpool."""
    clean_bytes, content_type, ext, width, height = validate_and_reencode_image(raw_bytes)
    storage_key, target_path = write_file_atomically(clean_bytes, ext, upload_dir=upload_dir)
    return storage_key, target_path, content_type, len(clean_bytes), width, height


class ProductImageService:
    def _get_product(self, db: Session, product_id: int) -> Product:
        product = product_repo.get_by_id(db, product_id)
        if not product:
            raise ProductNotFound(product_id)
        return product

    async def upload_image(
        self,
        db: Session,
        product_id: int,
        upload_file: UploadFile,
        user_id: int | None = None,
        alt_text: str | None = None,
        is_primary: bool = True,
        replace_existing: bool = True,
    ) -> ProductImage:
        product = self._get_product(db, product_id)
        raw_bytes = await read_upload_stream_bounded(upload_file, settings.UPLOAD_MAX_BYTES)
        sanitized_name = sanitize_original_filename(upload_file.filename)
        cleaned_alt = alt_text.strip()[:255] if alt_text and alt_text.strip() else product.name

        storage_key, target_path, content_type, size_bytes, width, height = await run_in_threadpool(
            _process_and_write_sync,
            raw_bytes,
            settings.UPLOAD_DIR,
        )

        old_files_to_remove: list[str] = []
        try:
            existing_images = (
                db.query(ProductImage)
                .filter(ProductImage.product_id == product.id)
                .order_by(ProductImage.sort_order.asc(), ProductImage.created_at.asc())
                .all()
            )
            if replace_existing and is_primary:
                for old_img in existing_images:
                    if old_img.is_primary:
                        old_files_to_remove.append(old_img.storage_key)
                        db.delete(old_img)
                db.flush()
            elif is_primary:
                for old_img in existing_images:
                    if old_img.is_primary:
                        old_img.is_primary = False
                db.flush()

            image_row = ProductImage(
                product_id=product.id,
                storage_key=storage_key,
                original_filename=sanitized_name,
                content_type=content_type,
                size_bytes=size_bytes,
                width=width,
                height=height,
                alt_text=cleaned_alt,
                is_primary=is_primary,
                sort_order=0 if is_primary else len(existing_images),
                created_by=user_id,
            )
            db.add(image_row)
            db.commit()
            db.refresh(image_row)
        except Exception:
            db.rollback()
            target_path.unlink(missing_ok=True)
            raise

        for old_key in old_files_to_remove:
            try:
                old_path = resolve_safe_storage_path(old_key, settings.UPLOAD_DIR)
                old_path.unlink(missing_ok=True)
            except Exception as exc:
                logger.warning("Could not delete replaced image file %s: %s", old_key, exc)

        logger.info(
            "Uploaded product image id=%s product_id=%s key=%s type=%s size=%s",
            image_row.id,
            product.id,
            storage_key,
            content_type,
            size_bytes,
        )
        return image_row

    def list_images(self, db: Session, product_id: int) -> list[ProductImage]:
        product = self._get_product(db, product_id)
        return list(product.images)

    def update_image(
        self,
        db: Session,
        product_id: int,
        image_id: uuid.UUID,
        request: UpdateProductImageRequest,
    ) -> ProductImage:
        self._get_product(db, product_id)
        image_row = (
            db.query(ProductImage)
            .filter(ProductImage.id == image_id, ProductImage.product_id == product_id)
            .first()
        )
        if not image_row:
            raise ProductImageNotFound(str(image_id))

        if request.alt_text is not None:
            image_row.alt_text = request.alt_text.strip()[:255] or None

        if request.sort_order is not None:
            image_row.sort_order = request.sort_order

        if request.is_primary is True and not image_row.is_primary:
            (
                db.query(ProductImage)
                .filter(
                    ProductImage.product_id == product_id,
                    ProductImage.id != image_id,
                    ProductImage.is_primary.is_(True),
                )
                .update({"is_primary": False}, synchronize_session="fetch")
            )
            db.flush()
            image_row.is_primary = True
        elif request.is_primary is False:
            image_row.is_primary = False

        db.commit()
        db.refresh(image_row)
        return image_row

    def delete_image(self, db: Session, product_id: int, image_id: uuid.UUID) -> None:
        self._get_product(db, product_id)
        image_row = (
            db.query(ProductImage)
            .filter(ProductImage.id == image_id, ProductImage.product_id == product_id)
            .first()
        )
        if not image_row:
            raise ProductImageNotFound(str(image_id))

        storage_key = image_row.storage_key
        was_primary = image_row.is_primary
        db.delete(image_row)
        db.flush()

        if was_primary:
            next_img = (
                db.query(ProductImage)
                .filter(ProductImage.product_id == product_id)
                .order_by(ProductImage.sort_order.asc(), ProductImage.created_at.asc())
                .first()
            )
            if next_img:
                next_img.is_primary = True

        db.commit()

        try:
            file_path = resolve_safe_storage_path(storage_key, settings.UPLOAD_DIR)
            file_path.unlink(missing_ok=True)
        except Exception as exc:
            logger.warning("Could not unlink deleted image %s: %s", storage_key, exc)

        logger.info("Deleted product image id=%s product_id=%s key=%s", image_id, product_id, storage_key)

    def get_media_file(self, db: Session, storage_key: str) -> tuple[Path, str]:
        """Resolve media file path and content_type for public serving."""
        file_path = resolve_safe_storage_path(storage_key, settings.UPLOAD_DIR)
        if not file_path.is_file():
            raise ProductImageNotFound(storage_key)

        row = db.query(ProductImage).filter(ProductImage.storage_key == storage_key).first()
        if row:
            content_type = row.content_type
        elif storage_key.endswith(".jpg"):
            content_type = "image/jpeg"
        elif storage_key.endswith(".png"):
            content_type = "image/png"
        else:
            content_type = "image/webp"

        return file_path, content_type
