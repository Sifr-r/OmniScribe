"""XLIFF 1.2 and 2.0 glossary parser."""

from __future__ import annotations

from ._common import (
    XmlElement,
    decode_source,
    entry_dict,
    finalize,
    iter_text,
    local_name,
    require_bytes,
    safe_xml_root,
)
from .summary import GlossaryImportSummary


def parse_xliff(
    data: bytes,
    *,
    encoding: str | None = None,
    source_lang: str = "en",
) -> GlossaryImportSummary:
    """Parse XLIFF 1.2 ``trans-unit`` and XLIFF 2 ``unit/segment`` pairs."""
    raw = require_bytes(data)
    text, used_encoding, warnings = decode_source(raw, encoding)
    try:
        root = safe_xml_root(raw)
    except ValueError as exc:
        if "DTD and external entities" in str(exc):
            raise
        root = safe_xml_root(text)
    entries: list[dict[str, object]] = []

    for unit in root.iter():
        if local_name(unit.tag) != "trans-unit":
            continue
        source = _child_text(unit, "source")
        target = _child_text(unit, "target")
        item = entry_dict(source, target)
        if item is not None:
            entries.append(item)

    if not entries:
        for unit in root.iter():
            if local_name(unit.tag) != "unit":
                continue
            for segment in unit.iter():
                if local_name(segment.tag) != "segment":
                    continue
                source = _child_text(segment, "source")
                target = _child_text(segment, "target")
                item = entry_dict(source, target)
                if item is not None:
                    entries.append(item)

    if not entries:
        raise ValueError("XLIFF source contains no source/target pairs.")
    return finalize(
        entries,
        format_name="xliff",
        encoding=used_encoding,
        warnings=warnings,
    )


def _child_text(element: XmlElement, wanted: str) -> str:
    for child in element:
        if local_name(child.tag) == wanted:
            return iter_text(child)
    return ""
