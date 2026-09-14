"""Shared de-duplication guards for tree writers.

Tree exporters (:mod:`omniscribe.core.writers.markdown`,
:mod:`omniscribe.core.writers.html`, :mod:`omniscribe.core.writers.docx_tree`)
and the chunker (:mod:`omniscribe.core.chunking.chunker`) all track which
table nodes they have already rendered by combining two keys: the Python
``id()`` of the node object and its ``block_id`` attribute. The pattern used
to be a byte-identical ``set[str | int]`` in every module (audit F15).

:class:`TableDedup` centralises that policy. A node is considered "seen" if
either key matches a previously recorded node, so the same logical table that
appears both inside a page and again at ``DocumentTree.tables`` is rendered
exactly once regardless of which iteration encounters it first.
"""

from __future__ import annotations

from typing import Any

__all__ = ["TableDedup"]


class TableDedup:
    """De-duplicates table nodes by both ``id()`` and ``block_id``.

    Usage::

        seen = TableDedup()
        for table in tree.tables:
            if table in seen:
                continue
            ...
            seen.add(table)

    The ``in`` operator reflects either key, so a fresh table node that shares
    a ``block_id`` with a previously rendered one is also skipped.
    """

    __slots__ = ("_ids",)

    def __init__(self) -> None:
        self._ids: set[int | str] = set()

    def add(self, table: Any) -> None:
        """Mark *table* as rendered (records both ``id(table)`` and ``block_id``)."""
        self._ids.add(id(table))
        bid = getattr(table, "block_id", None)
        if bid:
            self._ids.add(bid)

    def __contains__(self, table: Any) -> bool:
        """Return ``True`` if *table* (or its ``block_id``) was previously added."""
        if id(table) in self._ids:
            return True
        bid = getattr(table, "block_id", None)
        return bool(bid) and bid in self._ids
