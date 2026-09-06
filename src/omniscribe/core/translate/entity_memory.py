"""Document-level entity memory for translation.

Extracts proper nouns, dates, and named entities from the source text and
re-injects them as a context block in every subsequent chunk's translation
prompt. This is the single highest-leverage quality fix for the
"the protagonist's name drifts mid-document" problem.
"""

from __future__ import annotations

import re
from collections import Counter
from collections import Counter as CounterT
from dataclasses import dataclass, field

# Common stopwords (English-only stoplist — multilingual stopwords would
# bloat the per-chunk context block).
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "if",
        "while",
        "with",
        "without",
        "for",
        "from",
        "of",
        "on",
        "in",
        "at",
        "to",
        "by",
        "as",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "their",
        "there",
        "here",
        "where",
        "when",
        "who",
        "what",
        "which",
        "he",
        "she",
        "they",
        "them",
        "his",
        "her",
        "him",
        "we",
        "us",
        "our",
        "you",
        "your",
        "i",
        "me",
        "my",
        "mine",
    ]
)

# Naive date pattern: matches 2024-01-09, 1/9/2024, Jan 9 2007, etc.
_DATE_RE = re.compile(
    r"\b("
    r"\d{4}-\d{2}-\d{2}"  # 2024-01-09
    r"|\d{1,2}/\d{1,2}/\d{2,4}"  # 1/9/2024
    r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s*\d{2,4}"
    r")\b",
    re.IGNORECASE,
)

# Naive proper-noun pattern: capitalized words not at sentence start.
# This is approximate; for production a spaCy NER model would be better.
_PROPER_RE = re.compile(
    r"(?<![\.\!\?]\s)(?<!^)(?<=\s)([A-Z][a-zA-Z'\-]{1,}(?:\s+[A-Z][a-zA-Z'\-]{1,})*)"
)


@dataclass(slots=True)
class EntityMemory:
    """In-memory bag of named entities and dates extracted from a document.

    Buckets are frequency ``Counter``s so the prompt block can cap each
    section to the most frequent entities (audit: unbounded context
    blocks grew every chunk prompt with document size).
    """

    names: CounterT[str] = field(default_factory=Counter)
    dates: CounterT[str] = field(default_factory=Counter)
    acronyms: CounterT[str] = field(default_factory=Counter)

    def add_text(self, text: str) -> None:
        for m in _DATE_RE.findall(text):
            self.dates[m] += 1
        for m in _PROPER_RE.findall(text):
            if m.lower() in _STOPWORDS:
                continue
            # Acronyms (all-caps, length 2..6) get their own bucket.
            stripped = m.strip()
            if 2 <= len(stripped) <= 6 and stripped.isupper():
                self.acronyms[stripped] += 1
            else:
                self.names[stripped] += 1

    def merge(self, other: EntityMemory) -> EntityMemory:
        merged = EntityMemory(
            names=Counter(self.names) + Counter(other.names),
            dates=Counter(self.dates) + Counter(other.dates),
            acronyms=Counter(self.acronyms) + Counter(other.acronyms),
        )
        return merged

    def to_prompt_block(self, max_items: int | None = None) -> str:
        """Context block for a translation prompt.

        ``max_items`` caps each section to the most frequent entities
        (ties broken alphabetically) so document size doesn't inflate
        every chunk prompt.
        """
        parts: list[str] = []
        if self.names:
            lines = _top(self.names, max_items)
            if lines:
                parts.append(
                    "PROPER NOUNS (use these names consistently):\n"
                    + "\n".join(f"- {n}" for n in lines)
                )
        if self.dates:
            lines = _top(self.dates, max_items)
            if lines:
                parts.append(
                    "DATES (preserve the original date format when possible):\n"
                    + "\n".join(f"- {d}" for d in lines)
                )
        if self.acronyms:
            lines = _top(self.acronyms, max_items)
            if lines:
                parts.append(
                    "ACRONYMS (preserve capitalization):\n"
                    + "\n".join(f"- {a}" for a in lines)
                )
        return "\n\n".join(parts)

    def is_empty(self) -> bool:
        return not (self.names or self.dates or self.acronyms)


def _top(counter: CounterT[str], max_items: int | None) -> list[str]:
    items = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    if max_items is not None:
        items = items[: max(0, max_items)]
    return [name for name, _count in items]
