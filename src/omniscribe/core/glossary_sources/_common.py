"""Internal normalization helpers for glossary source adapters."""

from __future__ import annotations

import base64
import binascii
import re
from collections import Counter
from typing import Any

from defusedxml import ElementTree as _DefusedElementTree
from defusedxml.common import DefusedXmlException

from .encoding import decode_bytes
from .summary import GlossaryImportSummary

# `defusedxml.ElementTree.fromstring` returns an `xml.etree.ElementTree.Element`
# instance, but we avoid importing that name from the stdlib to keep the
# `use-defused-xml` Semgrep rule quiet. `Any` here costs a little static typing
# in exchange for not pulling in a parser that does not guard against XXE.
XmlElement = Any

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def require_bytes(data: bytes | bytearray | str | None) -> bytes:
    """Normalize parser input to bytes without guessing a text encoding."""
    if data is None:
        raise ValueError("Glossary source data is required.")
    if isinstance(data, str):
        return data.encode("utf-8")
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    raise ValueError("Glossary source must be bytes or text.")


def decode_source(
    data: bytes | bytearray | str,
    encoding: str | None = None,
) -> tuple[str, str, tuple[str, ...]]:
    """Decode source data and normalize a possible encoding warning."""
    raw = require_bytes(data)
    if encoding is None:
        from .encoding import detect_encoding

        used, warning = detect_encoding(raw)
        text, _used, _ignored = decode_bytes(raw, used)
    else:
        used = encoding
        warning = ""
        text, _used, _ignored = decode_bytes(raw, encoding)
    warnings = (warning,) if warning else ()
    return text, used, warnings


def entry_dict(
    source: Any,
    target: Any,
    *,
    case_sensitive: Any = False,
    notes: Any = "",
    group: Any = None,
) -> dict[str, object] | None:
    """Normalize one pair and discard empty or non-scalar values."""
    if source is None or target is None:
        return None
    source_text = str(source).strip()
    target_text = str(target).strip()
    if not source_text or not target_text:
        return None
    item: dict[str, object] = {
        "source": source_text,
        "target": target_text,
        "case_sensitive": parse_bool(case_sensitive),
        "notes": "" if notes is None else str(notes).strip(),
    }
    if group is not None and str(group).strip():
        item["group"] = str(group).strip()
    return item


def parse_bool(value: Any) -> bool:
    """Parse common CSV/JSON boolean spellings without truthiness surprises."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return False


def finalize(
    entries: list[dict[str, object]],
    *,
    format_name: str,
    encoding: str | None = None,
    warnings: tuple[str, ...] = (),
    source_uri: str | None = None,
) -> GlossaryImportSummary:
    """Create a summary and count repeated source terms."""
    counts = Counter(
        str(item.get("source", "")).casefold() for item in entries if item.get("source")
    )
    duplicates = sum(count - 1 for count in counts.values() if count > 1)
    return GlossaryImportSummary(
        entries=entries,
        format=format_name,
        source_uri=source_uri,
        encoding=encoding,
        warnings=warnings,
        duplicates=duplicates,
    )


def local_name(tag: str) -> str:
    """Return an XML tag's local name, independent of namespace."""
    return tag.rsplit("}", 1)[-1].lower()


def iter_text(element: XmlElement) -> str:
    """Collect text from an XML element, including inline child elements."""
    if element is None:
        return ""
    return "".join(element.itertext()).strip()


def language_matches(value: str | None, wanted: str) -> bool:
    """Compare language tags by exact tag or base language."""
    if not value:
        return False
    actual = value.replace("_", "-").split("-", 1)[0].lower()
    expected = wanted.replace("_", "-").split("-", 1)[0].lower()
    return actual == expected


def decode_base64(value: str) -> bytes:
    """Decode a strict inline base64 payload."""
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("inline_bytes_b64 is not valid base64.") from exc


def validate_identifier(value: str, field_name: str) -> str:
    """Validate a SQL identifier before it is quoted into a statement."""
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"Invalid SQL identifier for {field_name}.")
    return value


def safe_xml_root(data: bytes | str) -> XmlElement:
    """Parse XML while rejecting DTDs, entity declarations, and external refs.

    defusedxml drives the parse so the rejection happens at the expat
    level (audit P1-8), not via a substring pre-filter — the previous
    ``SYSTEM`` / ``<!DOCTYPE`` scan false-positived on ordinary
    glossary text content and could be bypassed by declarations past
    the scanned prefix.

    `defusedxml.ElementTree.fromstring` raises `DefusedXmlException` for
    DTD/external-entity violations and `xml.etree.ElementTree.ParseError`
    for malformed XML. We deliberately don't import `ParseError` (semgrep
    `use-defused-xml`) and rely on the broad `Exception` fallback below to
    convert any remaining parse failure into a domain-level ValueError.
    """
    try:
        root: XmlElement = _DefusedElementTree.fromstring(data, forbid_dtd=True)
        return root
    except DefusedXmlException as exc:
        raise ValueError(
            "DTD and external entities are not allowed in glossary XML."
        ) from exc
    except Exception as exc:
        # Covers xml.etree.ElementTree.ParseError and any other parse failure
        # without importing ParseError directly (semgrep use-defused-xml).
        raise ValueError(f"Invalid glossary XML: {exc}") from exc
