"""Leaf module for page-range string parsing.

Lives in `core/pdf` (not `core/workflows/utils`) so that both
`core/pdf/rasterizer.py` and `core/workflows/utils.py` can import
it without crossing the `rasterizer → workflows.utils → workflows
→ hybrid → pdf` import cycle that originally caused
`_parse_page_range_local` to be duplicated as a cycle-breaker.

The parser accepts strings like "1-3,5,7-9" and returns a list
of (start, end) page tuples (1-indexed, inclusive). Invalid
input returns None.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Final

_PAGE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^\s*(\d+)\s*(?:-\s*(\d+)\s*)?$")


def parse_page_range(spec: str) -> list[tuple[int, int]] | None:
    """Parse a page range string into a list of (start, end) tuples.

    Examples:
        "1-3,5,7-9" -> [(1, 3), (5, 5), (7, 9)]
        "1"        -> [(1, 1)]
        ""         -> []
        "abc"      -> None (invalid)
    """
    if not spec or not spec.strip():
        return []
    out: list[tuple[int, int]] = []
    for token in spec.split(","):
        m = _PAGE_PATTERN.match(token)
        if m is None:
            return None
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) is not None else start
        if start < 1 or end < start:
            return None
        out.append((start, end))
    return out


def parse_page_range_with_total(spec: str, total_pages: int) -> list[int]:
    """Parse a page range string into a sorted, 0-indexed, deduped page list.

    Composes :func:`parse_page_range` with the ``total_pages`` clamp and
    the ``ValueError`` on invalid input that the pre-leaf
    ``_parse_page_range_local`` / ``parse_page_range`` implementations
    used to provide. This is the function both
    :mod:`omniscribe.core.pdf.rasterizer` and
    :mod:`omniscribe.core.workflows.utils` re-export under the
    ``parse_page_range`` name — it owns the only remaining parser logic
    that used to live in the duplicated wrappers.

    Examples:
        "1-3,5,7-9", total=10 -> [0, 1, 2, 4, 6, 7, 8]
        "8-12",       total=5  -> []              # out of range
        "1,1,2-3,3",  total=5  -> [0, 1, 2]       # duplicates collapsed
        "abc",        total=10 -> raises ValueError
    """
    ranges = parse_page_range(spec)
    if ranges is None:
        raise ValueError(f"Invalid page range syntax: '{spec}'")
    pages: set[int] = set()
    for start, end in ranges:
        pages.update(range(start - 1, min(end, total_pages)))
    return sorted(pages)


def serialize_page_range(pages: Iterable[int]) -> str:
    """Serialize an iterable of 1-indexed page numbers into a compact range string.

    Consecutive numbers are collapsed into inclusive hyphenated ranges (e.g.
    ``1-3``), isolated numbers remain single digits, duplicates are removed,
    and output is sorted in ascending order.

    Examples:
        serialize_page_range([1, 2, 3, 5, 7, 8, 9]) -> "1-3,5,7-9"
        serialize_page_range([3, 1, 2]) -> "1-3"
        serialize_page_range([1, 1, 2]) -> "1-2"
        serialize_page_range([]) -> ""

    Raises:
        ValueError: if any page number is less than 1 or not an integer.
    """
    cleaned: list[int] = []
    for p in pages:
        if not isinstance(p, int) or isinstance(p, bool) or p < 1:
            raise ValueError(f"Page numbers must be positive integers >= 1, got {p!r}")
        cleaned.append(p)
    if not cleaned:
        return ""
    sorted_unique = sorted(set(cleaned))
    ranges: list[tuple[int, int]] = []
    start = sorted_unique[0]
    end = start
    for p in sorted_unique[1:]:
        if p == end + 1:
            end = p
        else:
            ranges.append((start, end))
            start = end = p
    ranges.append((start, end))
    return ",".join(f"{s}-{e}" if s != e else str(s) for s, e in ranges)


__all__ = [
    "parse_page_range",
    "parse_page_range_with_total",
    "serialize_page_range",
]
