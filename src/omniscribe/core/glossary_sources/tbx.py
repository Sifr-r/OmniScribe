"""TBX glossary parser."""

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


class TbxParser(XmlGlossaryParser):
    """TBX term-entry parser (format_name = "tbx")."""

    format_name = "tbx"

    def _extract_entries(
        self, root: XmlElement, source_lang: str
    ) -> Iterable[dict[str, object]]:
        entries: list[dict[str, object]] = []
        for term_entry in root.iter():
            if local_name(term_entry.tag) != "termentry":
                continue
            language_terms = _language_terms(term_entry)
            source_terms: list[str] = []
            target_terms: list[str] = []
            for language, terms in language_terms:
                if language_matches(language, source_lang):
                    source_terms.extend(terms)
                elif terms:
                    target_terms.extend(terms)
            if not source_terms and len(language_terms) >= 2:
                source_terms = language_terms[0][1]
                target_terms = language_terms[1][1]
            if not source_terms or not target_terms:
                continue
            for source in source_terms:
                item = entry_dict(source, target_terms[0])
                if item is not None:
                    entries.append(item)
        return entries


def parse_tbx(
    data: bytes,
    *,
    encoding: str | None = None,
    source_lang: str = "en",
) -> GlossaryImportSummary:
    """Parse TBX term entries into source/target term pairs."""
    return TbxParser().parse(data, encoding=encoding, source_lang=source_lang)


def _language_terms(
    term_entry: XmlElement,
) -> list[tuple[str | None, list[str]]]:
    result: list[tuple[str | None, list[str]]] = []
    for lang_set in term_entry:
        if local_name(lang_set.tag) != "langset":
            continue
        language = lang_set.attrib.get(_XML_LANG) or lang_set.attrib.get("lang")
        terms: list[str] = []
        for descendant in lang_set.iter():
            if local_name(descendant.tag) == "term":
                value = iter_text(descendant)
                if value:
                    terms.append(value)
        result.append((language, terms))
    return result
