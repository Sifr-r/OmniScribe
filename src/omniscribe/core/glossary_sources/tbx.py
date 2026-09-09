"""TBX glossary parser."""

from __future__ import annotations

from ._common import (
    XmlElement,
    decode_source,
    entry_dict,
    finalize,
    iter_text,
    language_matches,
    local_name,
    require_bytes,
    safe_xml_root,
)
from .summary import GlossaryImportSummary

_XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"


def parse_tbx(
    data: bytes,
    *,
    encoding: str | None = None,
    source_lang: str = "en",
) -> GlossaryImportSummary:
    """Parse TBX term entries into source/target term pairs."""
    raw = require_bytes(data)
    _text, used_encoding, warnings = decode_source(raw, encoding)
    root = safe_xml_root(raw)
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

    if not entries:
        raise ValueError("TBX source contains no bilingual term entries.")
    return finalize(
        entries,
        format_name="tbx",
        encoding=used_encoding,
        warnings=warnings,
    )


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
