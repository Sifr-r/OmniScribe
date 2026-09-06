"""Encoding detection helpers shared by glossary source parsers."""

from __future__ import annotations

import codecs
import importlib
from typing import Any

__all__ = ["decode_bytes", "detect_encoding", "read_text_auto_detect"]


def detect_encoding(data: bytes) -> tuple[str, str]:
    """Return ``(encoding, warning)`` for a byte payload.

    BOMs are checked first in descending order of prefix length (4-byte,
    3-byte, 2-byte), followed by valid UTF-8 decoding. If UTF-8 fails,
    ``chardet`` is used when available and confident (>= 0.60). As final
    fallbacks, ``windows-1252`` and ``iso-8859-1`` / ``latin-1`` are returned.
    """
    if data.startswith(b"\x00\x00\xfe\xff"):
        return "utf-32-be", ""
    if data.startswith(b"\xff\xfe\x00\x00"):
        return "utf-32-le", ""
    if data.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig", ""
    if data.startswith(b"\xfe\xff"):
        return "utf-16-be", ""
    if data.startswith(b"\xff\xfe"):
        return "utf-16-le", ""

    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    else:
        return "utf-8", ""

    chardet_module: Any = None
    try:
        chardet_module = importlib.import_module("chardet")
    except ImportError:
        chardet_module = None

    if chardet_module is not None:
        detect_callable = getattr(chardet_module, "detect", None)
        if callable(detect_callable):
            detected = detect_callable(data)
            candidate = detected.get("encoding") if isinstance(detected, dict) else None
            confidence = (
                detected.get("confidence", 0.0) if isinstance(detected, dict) else 0.0
            )
            if (
                isinstance(candidate, str)
                and isinstance(confidence, (int, float))
                and confidence >= 0.60
            ):
                candidate_norm = candidate.lower()
                try:
                    codecs.lookup(candidate_norm)
                    data.decode(candidate_norm)
                except (LookupError, UnicodeDecodeError):
                    pass
                else:
                    return (
                        candidate_norm,
                        f"Detected source encoding as {candidate_norm}.",
                    )

    try:
        data.decode("windows-1252")
    except UnicodeDecodeError:
        pass
    else:
        return "windows-1252", "Source was not valid UTF-8; decoded as windows-1252."

    try:
        data.decode("iso-8859-1")
    except UnicodeDecodeError:
        pass
    else:
        return "iso-8859-1", "Source was not valid UTF-8; decoded as iso-8859-1."

    return "latin-1", "Source was not valid UTF-8; decoded as latin-1."


def decode_bytes(data: bytes, encoding: str | None = None) -> tuple[str, str, str]:
    """Decode bytes and return text, encoding used, and a warning."""
    if not isinstance(data, (bytes, bytearray)):
        raise ValueError("Glossary source must be bytes.")
    raw = bytes(data)
    used = encoding
    warning = ""
    if used is None:
        used, warning = detect_encoding(raw)
    try:
        text = raw.decode(used)
    except (LookupError, UnicodeDecodeError) as exc:
        if isinstance(exc, LookupError):
            raise ValueError(f"Unknown encoding: {used}") from exc
        raise ValueError(f"Could not decode glossary source as {used}.") from exc
    if text.startswith("\ufeff"):
        text = text[1:]
    return text, used, warning


def read_text_auto_detect(
    data: bytes,
    encoding: str | None = None,
) -> tuple[str, str]:
    """Validate and decode a byte payload using auto-detected or supplied encoding.

    Parameters:
        data: Byte payload to decode (must be bytes or bytearray).
        encoding: Optional explicit encoding to use. If None, encoding is
            auto-detected via :func:`detect_encoding`.

    Returns:
        A tuple of ``(text, used_encoding)``.

    Raises:
        ValueError: If data is not bytes or bytearray, if the encoding is
            unknown, or if the bytes cannot be decoded.
    """
    text, used, _warning = decode_bytes(data, encoding)
    return text, used
