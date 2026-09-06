"""Composition and preview helpers for the LexiconStore.

Provides `merged_enabled_glossary` and `preview` functions that operate
directly on `LexiconStore` instances, plus re-exports `GlossaryNotFoundError`.
"""

from __future__ import annotations

from typing import Any

from omniscribe.core.lexicon.lancedb_store import GlossaryNotFoundError
from omniscribe.core.lexicon.store import (
    LexiconEntry,
    LexiconStore,
    normalize_term,
)
from omniscribe.core.translate.glossary import Glossary, GlossaryEntry


def _legacy_entry_from_lexicon(entry: LexiconEntry) -> GlossaryEntry:
    """Build a :class:`GlossaryEntry` from a :class:`LexiconEntry`."""
    return GlossaryEntry(
        source=entry.source_text,
        target=entry.target_text,
        case_sensitive=entry.case_sensitive,
        notes=entry.notes,
    )


def merged_enabled_glossary(store: LexiconStore) -> Glossary:
    """Build a fully-merged :class:`Glossary` from all enabled glossaries.

    The merge is last-wins (later entries override earlier ones). We sort
    by priority ASC so that the highest-priority glossary is the last writer
    and the effective winner. Keys use the shared ``normalize_term`` so
    merge and conflict-preview agree (ß/STRASSE are one term).
    """
    metas = [m for m in store.list_glossaries() if m.enabled]
    metas.sort(key=lambda m: m.priority)  # low -> high
    seen: dict[str, Any] = {}
    for meta in metas:
        for entry in store.list_entries(meta.id):
            key = normalize_term(entry.source_text)
            seen[key] = entry
    merged = Glossary(entries=[_legacy_entry_from_lexicon(e) for e in seen.values()])
    merged.source_format = "library"
    return merged


def preview(store: LexiconStore) -> dict[str, object]:
    """Return a conflict-detection summary across all enabled glossaries.

    For every source term that appears in more than one enabled glossary,
    returns the list of distinct target translations across those glossaries.
    ``count`` returns the number of deduplicated entries in the merged glossary.
    Keys use the shared ``normalize_term`` so the preview can't disagree
    with the merge about what constitutes the same term.
    """
    merged = merged_enabled_glossary(store)
    metas = [m for m in store.list_glossaries() if m.enabled]
    by_source: dict[str, list[tuple[str, str]]] = {}
    for meta in metas:
        for entry in store.list_entries(meta.id):
            source = normalize_term(entry.source_text)
            if not source:
                continue
            by_source.setdefault(source, []).append((meta.name, entry.target_text))

    conflicts: list[dict[str, object]] = []
    for source_key, values in sorted(by_source.items()):
        if len({name for name, _target in values}) < 2:
            continue
        targets: list[str] = []
        for _name, target in values:
            if target not in targets:
                targets.append(target)
        conflicts.append({"source": source_key, "targets": targets})

    return {
        "count": len(merged.entries),
        "conflicts": conflicts,
        "enabled_glossaries": [m.name for m in metas],
    }


__all__ = [
    "GlossaryNotFoundError",
    "merged_enabled_glossary",
    "preview",
]
