"""TMX 1.4 and later glossary parser."""

from __future__ import annotations

from collections.abc import Iterable

from ._base import XmlGlossaryParser
from ._common import (
    XmlElement,
    entry_dict,
    iter_text,
    language_matches,
    local_name,
)
from .summary import GlossaryImportSummary

_XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"


class TmxParser(XmlGlossaryParser):
    """TMX translation-unit parser (format_name = "tmx")."""

    format_name = "tmx"

    def _extract_entries(
        self, root: XmlElement, source_lang: str
    ) -> Iterable[dict[str, object]]:
        entries: list[dict[str, object]] = []
        for translation_unit in root.iter():
            if local_name(translation_unit.tag) != "tu":
                continue
            variants: list[tuple[str | None, str]] = []
            for variant in translation_unit:
                if local_name(variant.tag) != "tuv":
                    continue
                language = variant.attrib.get(_XML_LANG) or variant.attrib.get("lang")
                segment = next(
                    (child for child in variant if local_name(child.tag) == "seg"),
                    None,
                )
                value = iter_text(segment)
                if value:
                    variants.append((language, value))
            if not variants:
                continue
            source = next(
                (
                    value
                    for language, value in variants
                    if language_matches(language, source_lang)
                ),
                variants[0][1],
            )
            target = next(
                (
                    value
                    for language, value in variants
                    if value != source and not language_matches(language, source_lang)
                ),
                "",
            )
            item = entry_dict(source, target)
            if item is not None:
                entries.append(item)
        return entries

    def _empty_message(self) -> str:
        return "TMX source contains no bilingual translation units."


def parse_tmx(
    data: bytes,
    *,
    encoding: str | None = None,
    source_lang: str = "en",
) -> GlossaryImportSummary:
    """Pair each source-language TMX segment with the first target segment."""
    return TmxParser().parse(data, encoding=encoding, source_lang=source_lang)
