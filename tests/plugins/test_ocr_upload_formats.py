"""Upload-format contract: README claim ⇄ route allowlist ⇄ sniffer.

Regression coverage for goal-alignment finding 8: the README advertised
BMP/TIFF, the rasterizer and content-sniffer already handled them, but the
OCR route's content-type allowlist and magic-byte table rejected them, and
the client dropzone never offered them. A user following the README got a
415. These tests fail if the three sides drift apart again.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_README = _REPO_ROOT / "README.md"
_DROPZONE = (
    _REPO_ROOT
    / "client"
    / "lib"
    / "features"
    / "workstation"
    / "controls"
    / "upload_dropzone.dart"
)


@pytest.mark.parametrize(
    "head, expected",
    [
        # Classic TIFF, little- and big-endian, plus BigTIFF.
        (b"II\x2a\x00\x08\x00\x00\x00\x00\x00", "tiff"),
        (b"MM\x00\x2a\x00\x00\x00\x08\x00", "tiff"),
        (b"II\x2b\x00\x08\x00\x00\x00\x00\x00", "tiff"),
        (b"MM\x00\x2b\x00\x00\x00\x08\x00", "tiff"),
        # BMP: "BM" magic + file size.
        (b"BM\x36\x00\x00\x00\x00\x00\x00\x00", "bmp"),
        # A TIFF-looking prefix that is truncated to 3 bytes must not match.
        (b"II\x2a", None),
    ],
)
def test_sniff_format_recognises_tiff_and_bmp(head: bytes, expected: str | None):
    from omniscribe.plugins.ocr.plugin import _sniff_format

    assert _sniff_format(head) == expected


def test_tiff_and_bmp_are_in_the_mime_allowlist():
    from omniscribe.plugins.ocr.routes import _MIME_TO_FORMAT

    assert _MIME_TO_FORMAT["image/tiff"] == "tiff"
    assert _MIME_TO_FORMAT["image/bmp"] == "bmp"


def test_error_message_lists_every_supported_format():
    """The 415 text must not drift from the allowlist again."""
    from omniscribe.plugins.ocr.routes import _SUPPORTED_UPLOAD_FORMATS

    for name in ("PDF", "PNG", "JPEG", "WebP", "AVIF", "TIFF", "BMP"):
        assert name in _SUPPORTED_UPLOAD_FORMATS


def test_readme_never_over_promises_formats():
    """Everything the README promises must be accepted by the route.

    This is the direction that broke: the README advertised BMP/TIFF while
    the route returned 415. Under-promising (the route accepting DOCX, which
    the Format Support bullet does not name) is not a defect.
    """
    from omniscribe.plugins.ocr.routes import _MIME_TO_FORMAT

    readme = _README.read_text(encoding="utf-8")
    match = re.search(r"\*\*Format Support\*\*:\s*(.+)", readme)
    assert match is not None, "README Format Support bullet not found"
    claim = match.group(1).lower()

    # claim spelling -> internal route format name
    promised = {
        "pdf": "pdf",
        "png": "png",
        "jpeg": "jpeg",
        "webp": "webp",
        "avif": "avif",
        "tiff": "tiff",
        "bmp": "bmp",
    }
    accepted_formats = set(_MIME_TO_FORMAT.values())
    for spelling, name in promised.items():
        if spelling not in claim:
            continue  # not advertised -> nothing to prove
        assert name in accepted_formats, (
            f"README promises {spelling!r} but the route does not accept it"
        )


def test_client_dropzone_offers_every_image_format():
    dropzone = _DROPZONE.read_text(encoding="utf-8")
    match = re.search(r"supportedExtensions\s*=\s*\[(.*?)\]", dropzone, re.DOTALL)
    assert match is not None, "dropzone supportedExtensions not found"
    extensions = set(re.findall(r"'([^']+)'", match.group(1)))
    # The client must be able to select every image format the API accepts.
    assert {"tiff", "bmp"} <= extensions
    assert {"pdf", "png", "jpg", "webp", "avif"} <= extensions
