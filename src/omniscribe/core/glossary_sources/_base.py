"""Base class for XML-format glossary parsers (TBX, TMX, XLIFF).

The three XML parsers used to share an outer contract verbatim (audit F3):

    raw = require_bytes(data)
    text, used_encoding, warnings = decode_source(raw, encoding)
    root = safe_xml_root(raw)  # or safe_xml_root(text) on retry in xliff
    entries: list[dict[str, object]] = []
    # ... format-specific iteration ...
    if not entries:
        raise ValueError("<FORMAT> source contains no bilingual ...")
    return finalize(entries, format_name="<fmt>", encoding=used_encoding, warnings=warnings)

That scaffolding is folded into :meth:`XmlGlossaryParser.parse`; subclasses
only implement :meth:`_extract_entries` (and, for XLIFF, override
:meth:`_safe_root` and :meth:`_empty_message`).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, ClassVar

from ._common import (
    decode_source,
    finalize,
    require_bytes,
    safe_xml_root,
)

if TYPE_CHECKING:
    from defusedxml.ElementTree import XmlElement

    from .summary import GlossaryImportSummary

__all__ = ["XmlGlossaryParser"]


class XmlGlossaryParser:
    """Base class for XML-format glossary parsers.

    Subclasses set :attr:`format_name` (the value passed to
    :func:`omniscribe.core.glossary_sources._common.finalize`) and implement
    :meth:`_extract_entries` to walk their specific element shape.
    """

    format_name: ClassVar[str] = ""

    def parse(
        self,
        data: bytes | str,
        *,
        encoding: str | None = None,
        source_lang: str = "en",
    ) -> GlossaryImportSummary:
        """Decode *data*, parse XML, extract entries, and finalize a summary.

        Raises ``ValueError`` if the source yields no entries or contains
        a forbidden DTD / external entity (audit P1-8).
        """
        raw = require_bytes(data)
        text, used_encoding, warnings = decode_source(raw, encoding)
        root = self._safe_root(raw, text)
        entries = list(self._extract_entries(root, source_lang))
        if not entries:
            raise ValueError(self._empty_message())
        return finalize(
            entries,
            format_name=self.format_name,
            encoding=used_encoding,
            warnings=warnings,
        )

    def _safe_root(self, raw: bytes, text: str) -> XmlElement:
        """Parse the XML root. Override to fall back to decoded text (XLIFF)."""
        return safe_xml_root(raw)

    def _extract_entries(
        self, root: XmlElement, source_lang: str
    ) -> Iterable[dict[str, object]]:
        """Yield ``{source, target, ...}`` dicts for every term pair."""
        raise NotImplementedError

    def _empty_message(self) -> str:
        """Error raised when no entries were extracted."""
        return f"{self.format_name.upper()} source contains no bilingual term entries."
