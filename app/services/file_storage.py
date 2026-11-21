import unicodedata
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile, status

from app.core.config import get_settings

settings = get_settings()

ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg", "application/pdf"}
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".pdf"}

# Content-Type and the file extension both come from the client and both can
# be lies. These are the real leading bytes of the formats we accept, so a
# .png that is actually HTML (or an SVG, or a zip) is rejected at the door
# rather than stored and served back later.
_MAGIC_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"%PDF-", "application/pdf"),
)

# Read in bounded chunks so an oversized upload is rejected partway through
# instead of after the whole thing is already resident in memory.
_CHUNK_BYTES = 64 * 1024

# models.kyc.KycDocument.file_name is String(255).
_MAX_FILE_NAME_CHARS = 200


def _safe_file_name(raw: str | None, fallback: str) -> str:
    """Produce a display filename safe to store and echo back.

    This value is only ever shown to an Ops reviewer and used as the
    download name — it never touches the filesystem path (see save_upload,
    which generates its own UUID name). It still gets cleaned, because an
    unbounded or control-character-laden name would otherwise reach a
    String(255) column (a 500 on Postgres) and a Content-Disposition header.
    """
    if not raw:
        return fallback

    # Drop any directory component a client may have sent.
    name = Path(raw).name
    name = "".join(c for c in name if unicodedata.category(c)[0] != "C")
    name = name.replace('"', "").replace("\\", "").strip()
    if not name:
        return fallback

    if len(name) > _MAX_FILE_NAME_CHARS:
        suffix = Path(name).suffix[:10]
        name = name[: _MAX_FILE_NAME_CHARS - len(suffix)] + suffix
    return name


def _sniff_content_type(head: bytes) -> str | None:
    for signature, content_type in _MAGIC_SIGNATURES:
        if head.startswith(signature):
            return content_type
    return None


async def save_upload(file: UploadFile, subdir: str) -> str:
    """
    Validates and persists an uploaded file to local disk (Stage 1/2 dev
    storage). Never trusts the client: the declared content type, the
    extension AND the actual leading bytes all have to agree, and the stored
    filename is generated rather than derived from input.

    Production should replace this with private object storage plus
    short-lived signed URLs (see the KYC document security requirements).
    """
    extension = Path(file.filename or "").suffix.lower()
    if extension == ".jpg":
        extension = ".jpeg"

    if file.content_type not in ALLOWED_CONTENT_TYPES or extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported file type. Please upload a PNG, JPG, or PDF.",
        )

    max_bytes = settings.max_upload_mb * 1024 * 1024
    too_large = HTTPException(
        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        detail=f"File is too large. Maximum size is {settings.max_upload_mb}MB.",
    )

    target_dir = Path(settings.upload_dir) / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"{uuid.uuid4().hex}{extension}"

    total = 0
    first_chunk = True
    try:
        with target_path.open("wb") as handle:
            while chunk := await file.read(_CHUNK_BYTES):
                if first_chunk:
                    sniffed = _sniff_content_type(chunk)
                    if sniffed is None or sniffed != file.content_type:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail=(
                                "That file does not look like a valid PNG, JPG or PDF. "
                                "Please re-export it and try again."
                            ),
                        )
                    first_chunk = False

                total += len(chunk)
                if total > max_bytes:
                    raise too_large
                handle.write(chunk)

        if total == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="That file is empty. Please choose another."
            )
    except Exception:
        # Never leave a partial or rejected file behind on disk.
        target_path.unlink(missing_ok=True)
        raise

    return str(target_path)
