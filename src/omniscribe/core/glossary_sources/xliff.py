"""XLIFF 1.2 and 2.0 glossary parser."""

from __future__ import annotations

from collections.abc import Iterable

from ._base import XmlGlossaryParser
from ._common import (
    XmlElement,
    entry_dict,
    iter_text,
    local_name,
    safe_xml_root,
)
from .summary import GlossaryImportSummary


class XliffParser(XmlGlossaryParser):
    """XLIFF 1.2 ``trans-unit`` and XLIFF 2 ``unit/segment`` parser.

    Overrides :meth:`_safe_root` to fall back to the decoded text when the
    raw-bytes parse fails (preserves the legacy ``parse_xliff`` behaviour
    for sources whose declared encoding doesn't round-trip).
    """

    format_name = "xliff"

    def _safe_root(self, raw: bytes, text: str) -> XmlElement:
        try:
            return safe_xml_root(raw)
        except ValueError as exc:
            if "DTD and external entities" in str(exc):
                raise
            return safe_xml_root(text)

    def _extract_entries(
        self, root: XmlElement, source_lang: str
    ) -> Iterable[dict[str, object]]:
        entries: list[dict[str, object]] = []
        # XLIFF 1.2 path
        for unit in root.iter():
            if local_name(unit.tag) != "trans-unit":
                continue
            source = _child_text(unit, "source")
            target = _child_text(unit, "target")
            item = entry_dict(source, target)
            if item is not None:
                entries.append(item)
        # XLIFF 2.0 fallback when the 1.2 path yielded nothing
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
        return entries

    def _empty_message(self) -> str:
        return "XLIFF source contains no source/target pairs."


def parse_xliff(
    data: bytes,
    *,
    encoding: str | None = None,
    source_lang: str = "en",
) -> GlossaryImportSummary:
    """Parse XLIFF 1.2 ``trans-unit`` and XLIFF 2 ``unit/segment`` pairs."""
    return XliffParser().parse(data, encoding=encoding, source_lang=source_lang)


def _child_text(element: XmlElement, wanted: str) -> str:
    for child in element:
        if local_name(child.tag) == wanted:
            return iter_text(child)
    return ""
