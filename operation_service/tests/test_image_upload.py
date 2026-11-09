from decimal import Decimal
from io import BytesIO
from pathlib import Path

from PIL import Image
import pytest

from app.core.config import settings
from app.models.category import Category
from app.models.product import Product, ProductImage
from app.services.media_cleanup import find_or_remove_orphan_files
from coffee_testing import TEST_NAME_PREFIX, auth_header


def _make_image_bytes(
    fmt: str = "JPEG",
    size: tuple[int, int] = (64, 48),
    color: tuple[int, int, int] = (120, 80, 40),
    exif_bytes: bytes | None = None,
) -> bytes:
    buf = BytesIO()
    img = Image.new("RGB", size, color)
    save_kwargs = {}
    if exif_bytes is not None and fmt == "JPEG":
        save_kwargs["exif"] = exif_bytes
    img.save(buf, format=fmt, **save_kwargs)
    return buf.getvalue()


@pytest.fixture
def upload_dir(tmp_path, monkeypatch) -> Path:
    target = tmp_path / "uploads"
    target.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(target))
    return target


@pytest.fixture
def sample_product(db_session) -> Product:
    cat = Category(name=f"{TEST_NAME_PREFIX}ImgCat_{id(db_session)}", description="Image test category")
    db_session.add(cat)
    db_session.flush()

    prod = Product(
        name=f"{TEST_NAME_PREFIX}ImgLatte_{id(db_session)}",
        description="Rich espresso with steamed milk",
        price=Decimal("3.75"),
        stock_quantity=25,
        is_active=True,
        category_id=cat.id,
    )
    db_session.add(prod)
    db_session.commit()
    db_session.refresh(prod)
    return prod


def test_upload_success_and_public_media_read(client, db_session, sample_product, upload_dir):
    staff_headers = auth_header(sub=10, role="staff")
    jpeg_bytes = _make_image_bytes("JPEG", size=(80, 60))

    res = client.post(
        f"/product/{sample_product.id}/images",
        headers=staff_headers,
        files={"file": ("latte.jpg", jpeg_bytes, "image/jpeg")},
        data={"alt_text": "Hot Caramel Latte"},
    )
    assert res.status_code == 200, res.text
    payload = res.json()["data"]
    assert payload["product_id"] == sample_product.id
    assert payload["content_type"] == "image/jpeg"
    assert payload["width"] == 80
    assert payload["height"] == 60
    assert payload["alt_text"] == "Hot Caramel Latte"
    assert payload["is_primary"] is True
    assert payload["url"].startswith("/api/media/")
    storage_key = payload["storage_key"]
    assert (upload_dir / storage_key).is_file()

    # Verify ProductResponse includes image_url, image_alt, and images list
    prod_res = client.get(f"/product/{sample_product.id}")
    assert prod_res.status_code == 200
    prod_data = prod_res.json()["data"]
    assert prod_data["image_url"] == f"/api/media/{storage_key}"
    assert prod_data["image_alt"] == "Hot Caramel Latte"
    assert len(prod_data["images"]) == 1

    # Public media endpoint serves image with Cache-Control and X-Content-Type-Options: nosniff
    media_res = client.get(f"/media/{storage_key}")
    assert media_res.status_code == 200
    assert media_res.headers["content-type"] == "image/jpeg"
    assert media_res.headers["x-content-type-options"] == "nosniff"
    assert "max-age=31536000" in media_res.headers["cache-control"]


def test_upload_rejects_text_file_renamed_jpg_and_svg(client, sample_product, upload_dir):
    staff_headers = auth_header(sub=10, role="staff")

    # 1. Plain text renamed .jpg
    res_txt = client.post(
        f"/product/{sample_product.id}/images",
        headers=staff_headers,
        files={"file": ("sneaky.jpg", b"This is not a JPEG image at all", "image/jpeg")},
    )
    assert res_txt.status_code == 415
    assert res_txt.json()["error_code"] == "err_415_unsupported_image_type"

    # 2. SVG payload claiming to be image/png (must never be accepted)
    svg_bytes = b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"></svg>'
    res_svg = client.post(
        f"/product/{sample_product.id}/images",
        headers=staff_headers,
        files={"file": ("vector.png", svg_bytes, "image/png")},
    )
    assert res_svg.status_code == 415
    assert list(upload_dir.iterdir()) == []


def test_upload_detects_real_type_ignoring_fake_content_type_header(client, sample_product, upload_dir):
    staff_headers = auth_header(sub=10, role="staff")
    png_bytes = _make_image_bytes("PNG", size=(32, 32))

    # Send valid PNG with bogus Content-Type application/octet-stream and .bin extension
    res = client.post(
        f"/product/{sample_product.id}/images",
        headers=staff_headers,
        files={"file": ("mystery.bin", png_bytes, "application/octet-stream")},
    )
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["content_type"] == "image/png"
    assert data["storage_key"].endswith(".png")


def test_upload_rejects_oversized_file_with_413(client, sample_product, upload_dir, monkeypatch):
    monkeypatch.setattr(settings, "UPLOAD_MAX_BYTES", 256)  # 256-byte limit for test
    staff_headers = auth_header(sub=10, role="staff")
    large_png = _make_image_bytes("PNG", size=(200, 200)) + (b"\x00" * 1024)
    assert len(large_png) > 256

    res = client.post(
        f"/product/{sample_product.id}/images",
        headers=staff_headers,
        files={"file": ("large.png", large_png, "image/png")},
    )
    assert res.status_code == 413
    assert res.json()["error_code"] == "err_413_image_too_large"
    assert list(upload_dir.iterdir()) == []


def test_upload_rejects_corrupt_image_bytes(client, sample_product, upload_dir):
    staff_headers = auth_header(sub=10, role="staff")
    # Valid JPEG magic header followed by corrupt truncated data
    corrupt_jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01garbage_corrupt_stream"

    res = client.post(
        f"/product/{sample_product.id}/images",
        headers=staff_headers,
        files={"file": ("corrupt.jpg", corrupt_jpeg, "image/jpeg")},
    )
    assert res.status_code == 422
    assert res.json()["error_code"] == "err_422_invalid_image"
    assert list(upload_dir.iterdir()) == []


def test_upload_rejects_decompression_bomb_and_excessive_dimensions(
    client, sample_product, upload_dir, monkeypatch
):
    monkeypatch.setattr(settings, "UPLOAD_MAX_DIMENSION", 100)
    monkeypatch.setattr(settings, "UPLOAD_MAX_PIXELS", 10_000)
    staff_headers = auth_header(sub=10, role="staff")

    # 150x150 exceeds the 100x100 dimension cap and 10,000 pixel bomb limit
    oversized_dim_img = _make_image_bytes("PNG", size=(150, 150))
    res = client.post(
        f"/product/{sample_product.id}/images",
        headers=staff_headers,
        files={"file": ("bomb.png", oversized_dim_img, "image/png")},
    )
    assert res.status_code == 422
    assert res.json()["error_code"] == "err_422_invalid_image"
    assert list(upload_dir.iterdir()) == []


def test_upload_prevents_path_traversal_in_filename_and_media_route(
    client, sample_product, upload_dir
):
    staff_headers = auth_header(sub=10, role="staff")
    webp_bytes = _make_image_bytes("WEBP", size=(40, 40))

    res = client.post(
        f"/product/{sample_product.id}/images",
        headers=staff_headers,
        files={"file": ("../../etc/passwd.webp", webp_bytes, "image/webp")},
    )
    assert res.status_code == 200
    data = res.json()["data"]
    assert "/" not in data["storage_key"]
    assert "\\" not in data["storage_key"]
    assert data["original_filename"] == "passwd.webp"
    assert (upload_dir / data["storage_key"]).resolve().parent == upload_dir.resolve()

    # Attempting path traversal on GET /media/... must return 404
    traversal_res = client.get("/media/..%2F..%2Fetc%2Fpasswd")
    assert traversal_res.status_code == 404


def test_upload_requires_authentication_and_staff_or_admin_role(client, sample_product, upload_dir):
    jpeg_bytes = _make_image_bytes("JPEG", size=(32, 32))

    # 1. Missing token -> 401
    unauth_res = client.post(
        f"/product/{sample_product.id}/images",
        files={"file": ("latte.jpg", jpeg_bytes, "image/jpeg")},
    )
    assert unauth_res.status_code == 401

    # 2. Customer token -> 403
    customer_headers = auth_header(sub=201, role="customer")
    forbidden_res = client.post(
        f"/product/{sample_product.id}/images",
        headers=customer_headers,
        files={"file": ("latte.jpg", jpeg_bytes, "image/jpeg")},
    )
    assert forbidden_res.status_code == 403
    assert list(upload_dir.iterdir()) == []


def test_replace_and_delete_product_image_removes_old_file_and_row(
    client, db_session, sample_product, upload_dir
):
    admin_headers = auth_header(sub=1, role="admin")

    # 1. Upload first image
    first_res = client.post(
        f"/product/{sample_product.id}/images",
        headers=admin_headers,
        files={"file": ("first.jpg", _make_image_bytes("JPEG", color=(10, 20, 30)), "image/jpeg")},
        data={"alt_text": "First image"},
    )
    assert first_res.status_code == 200
    first_key = first_res.json()["data"]["storage_key"]
    assert (upload_dir / first_key).is_file()

    # 2. Upload replacement primary image -> old file and row are replaced
    second_res = client.post(
        f"/product/{sample_product.id}/images",
        headers=admin_headers,
        files={"file": ("second.png", _make_image_bytes("PNG", color=(40, 50, 60)), "image/png")},
        data={"alt_text": "Second image", "is_primary": "true", "replace_existing": "true"},
    )
    assert second_res.status_code == 200
    second_data = second_res.json()["data"]
    second_key = second_data["storage_key"]
    second_id = second_data["id"]

    assert not (upload_dir / first_key).exists()
    assert (upload_dir / second_key).is_file()

    # 3. Update alt_text via PATCH
    patch_res = client.patch(
        f"/product/{sample_product.id}/images/{second_id}",
        headers=admin_headers,
        json={"alt_text": "Updated Espresso Alt"},
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["data"]["alt_text"] == "Updated Espresso Alt"

    # 4. Delete image -> row and file on disk are removed
    del_res = client.delete(
        f"/product/{sample_product.id}/images/{second_id}",
        headers=admin_headers,
    )
    assert del_res.status_code == 200
    assert del_res.json()["data"]["deleted"] is True
    assert not (upload_dir / second_key).exists()
    assert (
        db_session.query(ProductImage)
        .filter(ProductImage.product_id == sample_product.id)
        .count()
        == 0
    )

    media_after_delete = client.get(f"/media/{second_key}")
    assert media_after_delete.status_code == 404


def test_failed_db_insert_cleans_up_written_file(
    client, db_session, sample_product, upload_dir, monkeypatch
):
    staff_headers = auth_header(sub=10, role="staff")

    def _boom_commit():
        raise RuntimeError("Simulated database commit failure")

    monkeypatch.setattr(db_session, "commit", _boom_commit)

    with pytest.raises(RuntimeError, match="Simulated database commit failure"):
        client.post(
            f"/product/{sample_product.id}/images",
            headers=staff_headers,
            files={"file": ("rollback.jpg", _make_image_bytes("JPEG"), "image/jpeg")},
        )

    # Ensure atomic rollback unlinked the file from upload_dir
    assert list(upload_dir.iterdir()) == []


def test_exif_metadata_is_stripped_on_upload(client, sample_product, upload_dir):
    staff_headers = auth_header(sub=10, role="staff")

    # Build a JPEG containing EXIF tags (Make=0x010F, ImageDescription=0x010E)
    exif = Image.Exif()
    exif[0x010F] = "SecretCameraVendor"
    exif[0x010E] = "GPS Secret Location Metadata"
    raw_with_exif = _make_image_bytes("JPEG", size=(50, 50), exif_bytes=exif.tobytes())

    # Sanity check that source bytes contain EXIF
    with Image.open(BytesIO(raw_with_exif)) as src_img:
        assert len(src_img.getexif()) > 0

    res = client.post(
        f"/product/{sample_product.id}/images",
        headers=staff_headers,
        files={"file": ("photo_with_exif.jpg", raw_with_exif, "image/jpeg")},
    )
    assert res.status_code == 200
    storage_key = res.json()["data"]["storage_key"]

    stored_bytes = (upload_dir / storage_key).read_bytes()
    assert b"SecretCameraVendor" not in stored_bytes
    assert b"GPS Secret Location" not in stored_bytes
    with Image.open(BytesIO(stored_bytes)) as clean_img:
        assert len(clean_img.getexif()) == 0


def test_orphan_media_cleanup_dry_run_and_delete(db_session, upload_dir):
    orphan_name = "0123456789abcdef0123456789abcdef.jpg"
    orphan_file = upload_dir / orphan_name
    orphan_file.write_bytes(b"orphan")

    # Dry run lists orphan without deleting it
    found_dry = find_or_remove_orphan_files(db_session, upload_dir=str(upload_dir), dry_run=True)
    assert found_dry == [orphan_name]
    assert orphan_file.exists()

    # Delete mode removes the orphan file
    found_del = find_or_remove_orphan_files(db_session, upload_dir=str(upload_dir), dry_run=False)
    assert found_del == [orphan_name]
    assert not orphan_file.exists()
