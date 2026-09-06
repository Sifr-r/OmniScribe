"""Query-term extraction for the lexicon vector leg.

Embedding a whole 4000-char chunk against a ~128-token MiniLM window
silently truncates most of the chunk (audit finding). The vector leg
therefore embeds a handful of *candidate terms* extracted from the
chunk — acronyms, quoted phrases, capitalized runs, non-Latin spans —
which is exactly the granularity glossaries contain.
"""

from __future__ import annotations

import re

_QUOTED_RE = re.compile(r'"([^"]{2,60})"')
_ACRONYM_RE = re.compile(r"\b[A-Z0-9]{2,8}\b")
_CAP_RUN_RE = re.compile(
    r"\b(?:[A-Z][a-zA-Z'\-]+(?:\s+(?:of|de|du|der|and|the)\s+)?){2,}\b"
)
_NON_LATIN_RE = re.compile(
    r"([\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
    r"\uac00-\ud7af\u0600-\u06ff\u0400-\u04ff]{2,20})"
)

# Acronym-shaped tokens that are really just function words.
_ACRONYM_DENYLIST = {"THE", "AND", "FOR", "NOT", "ALL", "ANY", "SEE", "PER"}


def candidate_terms(text: str, *, limit: int = 8) -> list[str]:
    """Extract glossary-shaped candidate terms from a chunk.

    Quoted phrases first, then acronyms, capitalized runs, and non-Latin
    spans. Deduped case-insensitively (first spelling wins), capped at
    ``limit``.
    """
    if not text or not text.strip():
        return []
    raw: list[str] = []
    raw.extend(m.group(1).strip() for m in _QUOTED_RE.finditer(text))
    raw.extend(
        m.group(0)
        for m in _ACRONYM_RE.finditer(text)
        if m.group(0) not in _ACRONYM_DENYLIST
    )
    raw.extend(m.group(0).strip() for m in _CAP_RUN_RE.finditer(text))
    raw.extend(m.group(1) for m in _NON_LATIN_RE.finditer(text))

    terms: list[str] = []
    seen: set[str] = set()
    for term in raw:
        key = term.casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        terms.append(term)
        if len(terms) >= limit:
            break
    return terms
