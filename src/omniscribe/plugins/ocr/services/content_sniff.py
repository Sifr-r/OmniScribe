"""Upload content-type sniffing for the OCR service.

Extracted from ``plugins/ocr/service.py`` in Phase 3.8 (4.8,
2026-09-05). Owns the file-extension inference for incoming uploads
so the rest of the OCR service can treat a normalized ``.pdf`` /
``.png`` / ``.jpeg`` / etc. suffix without caring how the magic-byte
detection was done.

Public surface:

- :func:`guess_suffix` — the single public entry point. Returns a
  lowercased, dot-prefixed extension string.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

#: Canonical mapping from MIME type to file extension. Used as the
#: fast path before falling back to :func:`mimetypes.guess_extension`
#: (which has surprising defaults for some image types).
_CONTENT_TYPE_TO_SUFFIX: dict[str, str] = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpeg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/avif": ".avif",
    "image/tiff": ".tiff",
    "image/bmp": ".bmp",
    "image/gif": ".gif",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "text/html": ".html",
    "text/markdown": ".md",
    "text/x-markdown": ".md",
}


def guess_suffix(filename: str, content_type: str | None = None) -> str:
    """Determine the file extension for an upload.

    Prefers the extension from ``filename`` if present. For
    extensionless uploads, inspects ``content_type`` (MIME type
    sniffing) before falling back to ``.pdf``.

    Fallback semantics (smell 6.81):
        When ``filename`` has no extension (e.g. ``"upload"`` or ``""``) and
        ``content_type`` is missing, unrecognized, generic
        (``application/octet-stream``, ``binary/octet-stream``), or resolves
        to ``.bin``, this function deliberately falls back to ``.pdf``.
        This default assumes incoming document uploads are PDFs unless
        indicated otherwise, matching the primary OCR ingest format.
    """
    suffix = Path(filename).suffix
    if suffix:
        return suffix
    if content_type:
        mime = content_type.split(";")[0].strip().lower()
        if mime in _CONTENT_TYPE_TO_SUFFIX:
            return _CONTENT_TYPE_TO_SUFFIX[mime]
        if mime not in ("application/octet-stream", "binary/octet-stream"):
            guessed = mimetypes.guess_extension(mime)
            if guessed and guessed != ".bin":
                return guessed
    return ".pdf"


__all__ = ["guess_suffix"]
