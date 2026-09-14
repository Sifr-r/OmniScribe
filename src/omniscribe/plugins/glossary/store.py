"""Lazy LexiconStore provider (optional `lexicon` extra).

`lancedb_store.py` hard-imports pyarrow at module top, so neither it nor
`omniscribe.core.lexicon` may be imported at THIS module's top level —
the plugin must boot in images without the lexicon extra. The provider
defers the runtime import to first use; routes surface 503 with the old
install hint when the store cannot be imported.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from omniscribe.core.lexicon import LexiconStore

_LOGGER = logging.getLogger("omniscribe.plugins.glossary")


class LexiconProvider:
    """Constructs the LanceDB store on first use; caches the result."""

    def __init__(self, store_path: Path) -> None:
        self._store_path = store_path
        self._store: LexiconStore | None = None
        self._tried = False

    def get(self) -> LexiconStore | None:
        # Pedantic 9.6: previously the ``_tried = True`` assignment ran
        # before the import attempt, so a single ``ImportError``
        # (operator runs the image without the ``lexicon`` extra) made
        # the provider permanent-cached to ``None`` and a later
        # ``uv sync --extra lexicon`` (e.g. the operator installed the
        # extra mid-run, or a health probe earlier faked an
        # ImportError) had no chance to recover. The flag is now set
        # only on success; ``ImportError`` leaves ``_tried`` False so
        # the next call retries the import.
        if self._tried:
            return self._store
        try:
            from omniscribe.core.lexicon import LanceDBLexiconStore

            self._store = LanceDBLexiconStore(path=self._store_path)
            self._tried = True
            _LOGGER.info("lexicon store ready at %s", self._store_path)
        except ImportError as exc:
            _LOGGER.warning("lexicon extra unavailable: %s", exc)
            self._store = None
            # ``_tried`` stays False; the next get() will retry.
        return self._store
