"""Shared base64-image helpers for the imaging, OCR, and workflow pipelines.

Audit F7 + F8 + F13 used to have three near-identical decoders:

- :func:`omniscribe.core.workflows.utils._decode_page_image` (private,
  ``.convert("RGB")``, no buffer-close guarantee)
- the bare ``Image.open(io.BytesIO(base64.b64decode(...))).convert(...)``
  chain in :mod:`omniscribe.core.ocr.processor`
- :func:`decode_base64_image` here (public, uses ``with`` to close the buffer)

This module is the single source of truth. Pass ``mode="L"`` (or any other
PIL mode string) to convert the decoded image in-place — the previous
``_decode_page_image`` helper is folded into this one via the default
``mode="RGB"`` that callers historically expected.

Canonical field name (audit F14)
--------------------------------

Across the OmniScribe codebase two spellings appear for "a base64-encoded
image string":

- ``image_b64`` — the canonical in-process pipeline name. Every function in
  :mod:`omniscribe.core.workflows`, :mod:`omniscribe.core.imaging`, and
  :mod:`omniscribe.core.grounded` that accepts a base64 image uses this
  spelling in its signature. It also matches the existing ``image_bytes_b64``
  field on :class:`FigureNode`.

- ``image_base64`` — only at the HTTP / wire-format boundary: the
  OpenAI-style multimodal ``image_url`` payloads, the Flutter client's
  request body, and the LLM client wrappers that translate between layers
  (:mod:`omniscribe.core.llm`, :mod:`omniscribe.core.ocr.chat_client`,
  :mod:`omniscribe.core.ocr.multi_format_client`). The bridge lives in
  :func:`omniscribe.core.llm.client.call_llm`.

When writing a new function: use ``image_b64`` unless you're crossing the
HTTP wire.
"""

from __future__ import annotations

import base64
import io

from PIL import Image

__all__ = ["decode_b64_bytes", "decode_base64_image", "encode_image_base64"]


def decode_b64_bytes(data: str, *, validate: bool = False) -> bytes:
    """Decode a base64 string to raw image bytes (no PIL involvement).

    ``validate=True`` mirrors :func:`base64.b64decode`'s strict mode — use it
    for inputs that come from a client (audit H1). Defaults to permissive
    decoding so internal callers don't pay the validation cost twice when the
    bytes are immediately handed to a PIL ``Image.open`` call below.
    """
    return base64.b64decode(data, validate=validate)


def decode_base64_image(data: str, *, mode: str | None = None) -> Image.Image:
    """Decode a base64 image string to a PIL :class:`Image`.

    Uses a ``with Image.open(...)`` block so the underlying buffer is closed
    on return. H1 audit fix: a bare ``Image.open(...).convert(...)`` chain
    keeps the buffer alive until GC, which leaks memory in long-running OCR
    runs (200+ pages). The ``with`` block makes cleanup intent explicit and
    protects against future refactors that change the call pattern.

    If ``mode`` is set (e.g. ``"RGB"`` or ``"L"``), the returned image is
    converted to that mode before being copied out of the ``with`` block.
    ``None`` preserves the source mode.
    """
    raw = base64.b64decode(data)
    with Image.open(io.BytesIO(raw)) as img:
        img.load()
        out = img.convert(mode) if mode else img
        return out.copy()


def encode_image_base64(img: Image.Image, format: str = "PNG") -> str:
    """Encodes a PIL Image to a base64 string."""
    buf = io.BytesIO()
    img.save(buf, format=format)
    return base64.b64encode(buf.getvalue()).decode("ascii")
