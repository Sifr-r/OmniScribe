"""Tests for LexiconSchemaManager table-open and meta-compatibility fail-closed behavior."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

# ``pyarrow`` ships only in the ``memory``/``lexicon`` extras, and neither
# ``test.yml`` nor ``nightly.yml`` installs them — both run
# ``uv sync --extra web [--extra async-translation]``. Without this guard the
# bare ``import pyarrow`` below made the *whole* suite fail at collection in
# both workflows, so no fast-tier pytest run could ever reach the assertions.
# The sibling lexicon tests (``test_recall_fixture.py``,
# ``test_toggle_glossary_atomic.py``,
# ``test_translation_lexicon_integration.py``) already skip on this import;
# this module was simply missed. Keep the guard *before* the
# ``omniscribe.core.lexicon.schema`` import, which hard-imports pyarrow too.
pytest.importorskip("pyarrow")

import pyarrow as pa

from omniscribe.core.lexicon.schema import (
    EmbeddingModelMismatchError,
    LexiconSchemaManager,
)


class DummyEmbeddingModel:
    def __init__(
        self, model_name: str = "text-embedding-3-small", dim: int = 384
    ) -> None:
        self.model_name = model_name
        self.dim = dim


def _dummy_clock() -> datetime:
    return datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def test_open_terms_table_fails_closed_on_open_error() -> None:
    """[P1-4] Table-open failures must fail closed and re-raise rather than overwrite."""
    manager = LexiconSchemaManager()
    db = MagicMock()
    db.list_tables.return_value = ["terms", "_meta"]
    db.open_table.side_effect = RuntimeError("Disk I/O error opening table")

    model = DummyEmbeddingModel()
    with pytest.raises(RuntimeError, match="Disk I/O error opening table"):
        manager.open_terms_table(db, model, _dummy_clock)

    # Must NOT have attempted to recreate or overwrite the table
    db.create_table.assert_not_called()


def test_open_terms_table_creates_table_when_missing() -> None:
    manager = LexiconSchemaManager()
    db = MagicMock()
    db.list_tables.return_value = []
    fake_table = MagicMock()
    fake_table.schema.names = ["entry_hash"]
    fake_table.count_rows.return_value = 0
    db.create_table.return_value = fake_table

    model = DummyEmbeddingModel()
    table = manager.open_terms_table(db, model, _dummy_clock)
    assert table is fake_table
    db.create_table.assert_any_call("terms", schema=manager._schema, mode="create")


def test_ensure_meta_and_compat_fails_closed_on_meta_open_error() -> None:
    """[P1-5] Meta table open errors must fail closed and re-raise rather than overwrite."""
    manager = LexiconSchemaManager()
    db = MagicMock()
    db.open_table.side_effect = OSError("Corrupted meta table block")

    model = DummyEmbeddingModel()
    with pytest.raises(IOError, match="Corrupted meta table block"):
        manager.ensure_meta_and_compat(
            db=db,
            existing_tables={"terms", "_meta"},
            embedding_model=model,
            clock=_dummy_clock,
        )

    db.create_table.assert_not_called()


def test_ensure_meta_and_compat_fails_closed_on_meta_read_error() -> None:
    """[P1-5] Meta table read errors must fail closed and re-raise rather than overwrite."""
    manager = LexiconSchemaManager()
    db = MagicMock()
    meta_table = MagicMock()
    meta_table.to_arrow.side_effect = pa.ArrowInvalid("Corrupted arrow data")
    db.open_table.return_value = meta_table

    model = DummyEmbeddingModel()
    with pytest.raises(pa.ArrowInvalid, match="Corrupted arrow data"):
        manager.ensure_meta_and_compat(
            db=db,
            existing_tables={"terms", "_meta"},
            embedding_model=model,
            clock=_dummy_clock,
        )

    db.create_table.assert_not_called()


def test_ensure_meta_and_compat_detects_mismatch() -> None:
    manager = LexiconSchemaManager()
    db = MagicMock()
    meta_table = MagicMock()
    fake_arrow = MagicMock()
    fake_arrow.to_pylist.return_value = [{"model_name": "old-model", "dim": 384}]
    meta_table.to_arrow.return_value = fake_arrow
    db.open_table.return_value = meta_table

    model = DummyEmbeddingModel(model_name="new-model", dim=384)
    with pytest.raises(
        EmbeddingModelMismatchError, match="Vector spaces are incompatible"
    ):
        manager.ensure_meta_and_compat(
            db=db,
            existing_tables={"terms", "_meta"},
            embedding_model=model,
            clock=_dummy_clock,
        )


def test_ensure_meta_and_compat_creates_meta_when_absent() -> None:
    manager = LexiconSchemaManager()
    db = MagicMock()
    model = DummyEmbeddingModel()

    manager.ensure_meta_and_compat(
        db=db,
        existing_tables={"terms"},
        embedding_model=model,
        clock=_dummy_clock,
    )

    db.create_table.assert_called_once()
    assert db.create_table.call_args[1]["mode"] == "create"
    assert db.create_table.call_args[0][0] == "_meta"
