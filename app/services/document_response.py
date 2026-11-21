"""Safe file responses for stored KYC / proof documents."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import HTTPException, status
from fastapi.responses import FileResponse

# The only types save_upload will ever have written. Derived from the stored
# path's extension, never from anything the client said at upload time.
_EXTENSION_CONTENT_TYPES = {
    ".png": "image/png",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".pdf": "application/pdf",
}


def document_response(
    file_path: str,
    download_name: str,
    *,
    disposition: Literal["attachment", "inline"] = "attachment",
) -> FileResponse:
    """Serve a stored document.

    Three things matter here:

    * ``media_type`` is pinned from the stored extension rather than guessed,
      so a file whose bytes were something unexpected cannot be served under
      a type the browser will execute. Combined with the global
      ``X-Content-Type-Options: nosniff`` header, there is no sniffing path.
    * ``attachment`` (the default) makes the browser download rather than
      render. ``inline`` is only for the Ops in-page document viewer, and is
      safe here because the API's Content-Security-Policy is
      ``default-src 'none'; sandbox`` — a rendered document gets no script
      execution, no embedding and no outbound requests.
    * A missing file is a 404, not the 500 that FileResponse raises when it
      cannot open the path (which also leaks the server path in logs tied to
      a user request).
    """
    path = Path(file_path)
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")

    media_type = _EXTENSION_CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")

    return FileResponse(
        path,
        media_type=media_type,
        filename=download_name,
        content_disposition_type=disposition,
        headers={"cache-control": "no-store", "x-content-type-options": "nosniff"},
    )
