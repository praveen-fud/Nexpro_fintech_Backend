import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile, status

from app.core.config import get_settings

settings = get_settings()

ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg", "application/pdf"}
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".pdf"}


async def save_upload(file: UploadFile, subdir: str) -> str:
    """
    Validates and persists an uploaded file to local disk (Stage 1/2 dev
    storage). Never trusts the filename extension alone — content_type is
    checked too. Production should replace this with private object storage
    and signed URLs (see KYC document security requirements).
    """
    extension = Path(file.filename or "").suffix.lower()
    if file.content_type not in ALLOWED_CONTENT_TYPES or extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported file type. Please upload a PNG, JPG, or PDF.",
        )

    contents = await file.read()
    if len(contents) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File is too large. Maximum size is {settings.max_upload_mb}MB.",
        )

    target_dir = Path(settings.upload_dir) / subdir
    target_dir.mkdir(parents=True, exist_ok=True)

    safe_name = f"{uuid.uuid4().hex}{extension}"
    target_path = target_dir / safe_name
    target_path.write_bytes(contents)

    return str(target_path)
