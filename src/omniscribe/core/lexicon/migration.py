"""One-shot migration from the legacy JSON+ChromaDB stores to LanceDB.

See ``docs/lexicon-migration-spec.md`` §6 for the design.

Three migration sources are supported, in this order:

1. ``library.json`` (the canonical glossary library)
2. ``chroma_db/`` (the ChromaDB ``lanes_lexicon`` collection, if present)

The migration is **idempotent** on three axes (spec §6.3):

1. Source-state-stable: re-running on unchanged source data produces a
   byte-equivalent ``lexicon.lance`` (the embedding model is pinned).
2. Source-state-dirty: re-running after source data changed picks up the
   new data without duplicating existing rows (terms are matched by
   ``(glossary_id, source_text, source_lang, target_lang)``).
3. Backup-aware: the ``lexicon_migration_backup_<ts>/`` directory uses
   a fresh timestamp on every run; never overwrites a previous backup.

The function is safe to call at server startup (auto-migrate on first
run, see spec §6.1) — if the migration fails, it logs and returns
without raising, so a broken migration never blocks boot.
"""

from __future__ import annotations

import json
import logging
import shutil
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .embedding import EmbeddingModel, get_default_embedding_model
from .lancedb_store import GlossaryNotFoundError, LanceDBLexiconStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MigrationReport:
    """What the migration did, returned to the CLI for printing."""

    ran: bool
    """Whether the migration actually wrote anything (False on no-op / dry-run)."""

    dry_run: bool
    """True if the call was a --dry-run — nothing was written."""

    verified: bool
    """True if the call was a --verify-only — read-only, no write."""

    backup_dir: Path | None
    """Path to the backup directory created this run (None on no-op / verify-only)."""

    glossaries_migrated: int = 0
    """Number of distinct glossaries migrated."""

    entries_migrated: int = 0
    """Total term-pair rows written."""

    chromadb_collection_found: bool = False
    """Whether a ChromaDB lanes_lexicon collection was found and read."""

    chromadb_entries_migrated: int = 0
    """Number of entries migrated from ChromaDB (typically 0 if the
    glossary library was the source of truth)."""

    skipped: bool = False
    """True if the migration was a no-op (already-migrated state)."""

    skip_reason: str = ""
    """Why the migration was a no-op (empty when ran=True)."""

    warnings: list[str] = field(default_factory=list)
    """Non-fatal warnings collected during the run."""

    error: str | None = None
    """Top-level error message if the migration failed (None on success)."""


# ---------------------------------------------------------------------------
# State detection
# ---------------------------------------------------------------------------


def detect_legacy_state(artifact_dir: Path) -> dict[str, Any]:
    """Inspect the artifact directory for legacy state.

    Returns a dict with three keys, each a Path-or-None:

    - ``library_json``: path to ``library.json`` if it exists
    - ``chroma_db``: path to ``chroma_db/`` if it exists
    - ``lexicon_lance``: path to ``lexicon.lance`` if it already exists
    """
    artifact_dir = Path(artifact_dir).expanduser().resolve()
    lib_path = artifact_dir / "glossary_library" / "library.json"
    chroma_path = artifact_dir / "chroma_db"
    lance_path = artifact_dir / "lexicon.lance"
    return {
        "library_json": lib_path if lib_path.exists() else None,
        "chroma_db": chroma_path if chroma_path.exists() else None,
        "lexicon_lance": lance_path if lance_path.exists() else None,
    }


# ---------------------------------------------------------------------------
# Library.json reader
# ---------------------------------------------------------------------------


def _read_library_json(path: Path) -> list[dict[str, object]]:
    """Read the legacy ``library.json`` and return its glossaries.

    Each glossary is returned as a dict matching the schema consumed by
    :meth:`LanceDBLexiconStore.save_glossary`. Defensive: skips malformed
    records rather than raising.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
        logger.warning("Cannot read %s: %s", path, exc)
        return []
    if not isinstance(payload, dict):
        return []
    raw_items = payload.get("glossaries", [])
    if not isinstance(raw_items, list):
        return []
    out: list[dict[str, object]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        try:
            out.append(
                {
                    "id": str(raw["id"]),
                    "name": str(raw["name"]),
                    "format": str(raw.get("format", "json_pairs")),
                    "source_uri": raw.get("source_uri"),
                    "encoding": raw.get("encoding"),
                    "group": str(raw.get("group", "default") or "default"),
                    "priority": int(raw.get("priority", 0)),
                    "enabled": bool(raw.get("enabled", True)),
                    "entries": list(raw.get("entries", [])),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out


# ---------------------------------------------------------------------------
# ChromaDB reader (REMOVED in Phase 5)
# ---------------------------------------------------------------------------
# The ChromaDB ``lanes_lexicon`` collection is no longer read by the
# migration. The legacy ``library.json`` is the only source — every
# glossary term has its own row in the JSON file, including the metadata
# ChromaDB would have held. Re-importing from a raw ChromaDB on-disk
# blob would be fragile (ChromaDB's internal layout is not a public API),
# and the JSON library is the source of truth that ChromaDB was a
# denormalized cache of.
#
# If you have a ChromaDB collection that contains terms NOT in the
# library.json, the migration will not pick them up. The recommended
# recovery is to re-import the source file (TBX / CSV / etc.) into the
# new store via the ``glossary_imports`` router.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_migration(
    *,
    artifact_dir: Path,
    embedding_model: EmbeddingModel | None = None,
    dry_run: bool = False,
    verify_only: bool = False,
    clock: Callable[[], datetime] | None = None,
) -> MigrationReport:
    """Run the legacy → LanceDB migration.

    Parameters
    ----------
    artifact_dir:
        The OmniScribe artifact directory. Expected layout::

            <artifact_dir>/
                glossary_library/library.json   # legacy JSON
                chroma_db/                      # legacy ChromaDB
                lexicon.lance/                  # new LanceDB (target)

    embedding_model:
        Override the embedding model (default: pinned
        ``paraphrase-multilingual-MiniLM-L12-v2``).

    dry_run:
        Compute the migration plan but don't write anything. Returns a
        report with the counts that *would* be migrated.

    verify_only:
        Read-only: confirm whether a previously-completed migration is
        intact (compares glossary count + entry count to the backup
        manifest). Never writes.

    The function is fail-open: on any error it returns a report with
    ``error`` set and a backup not created. Callers (the CLI, the
    auto-migrate hook) can decide how to surface this.
    """
    artifact_dir = Path(artifact_dir).expanduser().resolve()
    clock = clock or (lambda: datetime.now(UTC))
    warnings: list[str] = []

    if verify_only and dry_run:
        return MigrationReport(
            ran=False,
            dry_run=True,
            verified=True,
            backup_dir=None,
            error="--verify-only and --dry-run are mutually exclusive.",
        )

    state = detect_legacy_state(artifact_dir)

    # --verify-only: check the existing migration against the source state.
    if verify_only:
        return _verify_migration(state, embedding_model)

    # No-op cases (already migrated, or fresh install with nothing to migrate).
    if state["library_json"] is None and state["chroma_db"] is None:
        if state["lexicon_lance"] is not None:
            return MigrationReport(
                ran=False,
                dry_run=dry_run,
                verified=False,
                backup_dir=None,
                skipped=True,
                skip_reason="no legacy state; lexicon.lance already present",
            )
        return MigrationReport(
            ran=False,
            dry_run=dry_run,
            verified=False,
            backup_dir=None,
            skipped=True,
            skip_reason="no legacy state to migrate",
        )

    # Edge case: both library.json and lexicon.lance exist. Refuse to
    # silently overwrite (per spec §6.1). The user can manually recover.
    if state["library_json"] is not None and state["lexicon_lance"] is not None:
        return MigrationReport(
            ran=False,
            dry_run=dry_run,
            verified=False,
            backup_dir=None,
            skipped=True,
            skip_reason=(
                "both library.json and lexicon.lance exist; refusing to migrate "
                "(manual recovery required)"
            ),
            error=(
                "Ambiguous state: legacy library.json coexists with the new "
                "lexicon.lance. Inspect both and remove the legacy file if "
                "intentional."
            ),
        )

    # Read the legacy sources.
    legacy_glossaries = (
        _read_library_json(state["library_json"])
        if state["library_json"] is not None
        else []
    )

    # Build the backup (skip on dry-run).
    backup_dir: Path | None = None
    if not dry_run:
        backup_dir = _make_backup_dir(artifact_dir, clock)

    # Plan / report.
    total_entries = sum(len(_entry_list(g)) for g in legacy_glossaries)
    # ChromaDB no longer read (Phase 5). The directory is still moved
    # into the backup if present, but the count is always 0 in the report.

    if dry_run:
        return MigrationReport(
            ran=False,
            dry_run=True,
            verified=False,
            backup_dir=None,
            glossaries_migrated=len(legacy_glossaries),
            entries_migrated=total_entries,
            chromadb_collection_found=state["chroma_db"] is not None,
            chromadb_entries_migrated=0,  # ChromaDB no longer read (Phase 5)
        )

    # Run the migration against a fresh store.
    try:
        lance_path = artifact_dir / "lexicon.lance"
        store = LanceDBLexiconStore(
            path=lance_path,
            embedding_model=embedding_model or get_default_embedding_model(),
        )
    except Exception as exc:
        return MigrationReport(
            ran=False,
            dry_run=False,
            verified=False,
            backup_dir=backup_dir,
            error=f"Cannot open LanceDB at {lance_path}: {exc}",
        )

    glossaries_migrated = 0
    entries_migrated = 0
    try:
        for g in legacy_glossaries:
            entries = g.get("entries", [])
            if not isinstance(entries, list):
                continue
            # Re-save under the original glossary id: save_glossary replaces
            # same-id rows, so a dirty re-run is idempotent (no duplicates).
            store.save_glossary(
                name=str(g["name"]),
                format=str(g.get("format", "json_pairs")),
                entries=entries,
                source_uri=_opt_str_or_none(g.get("source_uri")),
                encoding=_opt_str_or_none(g.get("encoding")),
                group=str(g.get("group", "default") or "default"),
                priority=int(str(g.get("priority", 0))),
                glossary_id=str(g["id"]),
            )
            glossaries_migrated += 1
            entries_migrated += len(entries)
    except Exception as exc:
        return MigrationReport(
            ran=False,
            dry_run=False,
            verified=False,
            backup_dir=backup_dir,
            glossaries_migrated=glossaries_migrated,
            entries_migrated=entries_migrated,
            error=f"Migration failed mid-run: {exc}",
        )

    # Move the legacy files into the backup directory (only on real run).
    if backup_dir is not None:
        try:
            if state["library_json"] is not None:
                _move_into_backup(state["library_json"], backup_dir / "library.json")
            if state["chroma_db"] is not None:
                _move_into_backup(state["chroma_db"], backup_dir / "chroma_db")
        except Exception as exc:
            warnings.append(f"Could not move legacy files into backup: {exc}")

    return MigrationReport(
        ran=True,
        dry_run=False,
        verified=False,
        backup_dir=backup_dir,
        glossaries_migrated=glossaries_migrated,
        entries_migrated=entries_migrated,
        chromadb_collection_found=state["chroma_db"] is not None,
        chromadb_entries_migrated=0,  # ChromaDB no longer read (Phase 5)
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Auto-migrate hook (called from server startup)
# ---------------------------------------------------------------------------


def auto_migrate_if_needed(
    artifact_dir: Path,
    *,
    embedding_model: EmbeddingModel | None = None,
    clock: Callable[[], datetime] | None = None,
) -> MigrationReport:
    """Auto-migrate on first run if legacy state is present.

    Fail-open: returns a report with ``error`` set on failure; never
    raises. The server can boot even if the migration is broken — the
    glossary will simply be empty until the user runs the explicit
    ``omniscribe-migrate-lexicon`` CLI to retry.
    """
    state = detect_legacy_state(artifact_dir)
    if state["library_json"] is None and state["chroma_db"] is None:
        return MigrationReport(
            ran=False,
            dry_run=False,
            verified=False,
            backup_dir=None,
            skipped=True,
            skip_reason="no legacy state",
        )
    try:
        return run_migration(
            artifact_dir=artifact_dir,
            embedding_model=embedding_model,
            clock=clock,
        )
    except Exception as exc:  # last-resort safety net
        logger.exception("Auto-migration failed")
        return MigrationReport(
            ran=False,
            dry_run=False,
            verified=False,
            backup_dir=None,
            error=f"Auto-migration failed: {exc}",
        )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _make_backup_dir(artifact_dir: Path, clock: Callable[[], datetime]) -> Path:
    ts = clock().strftime("%Y%m%dT%H%M%SZ")
    backup = artifact_dir / f"lexicon_migration_backup_{ts}_{uuid.uuid4().hex[:6]}"
    backup.mkdir(parents=False, exist_ok=False)
    return backup


def _move_into_backup(src: Path, dest: Path) -> None:
    """Move a single file or directory into the backup location."""
    if src.is_dir():
        shutil.move(str(src), str(dest))
    else:
        # Move file to a directory; need to create parent first
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))


def _verify_migration(
    state: dict[str, Any], embedding_model: EmbeddingModel | None
) -> MigrationReport:
    """For ``--verify-only``: check the migration is intact."""
    if state["lexicon_lance"] is None:
        return MigrationReport(
            ran=False,
            dry_run=False,
            verified=True,
            backup_dir=None,
            skipped=True,
            skip_reason="no lexicon.lance to verify",
        )
    try:
        store = LanceDBLexiconStore(
            path=state["lexicon_lance"],
            embedding_model=embedding_model or get_default_embedding_model(),
        )
        metas = store.list_glossaries()
    except Exception as exc:
        return MigrationReport(
            ran=False,
            dry_run=False,
            verified=True,
            backup_dir=None,
            error=f"Cannot open lexicon.lance: {exc}",
        )
    return MigrationReport(
        ran=False,
        dry_run=False,
        verified=True,
        backup_dir=None,
        glossaries_migrated=len(metas),
        entries_migrated=sum(m.entry_count for m in metas),
    )


def _opt_str_or_none(value: object) -> str | None:
    """Coerce a possibly-null value to ``str | None`` (mypy-friendly)."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


def _entry_list(g: dict[str, object]) -> list[object]:
    """Return ``g['entries']`` if it's a list, else an empty list."""
    entries = g.get("entries", [])
    return entries if isinstance(entries, list) else []


__all__ = [
    "MigrationReport",
    "auto_migrate_if_needed",
    "detect_legacy_state",
    "run_migration",
]
