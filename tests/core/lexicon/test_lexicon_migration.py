"""Tests for the legacy → LanceDB migration (Phase 2).

Acceptance: migration is idempotent, leaves a backup, refuses on ambiguous
state, supports --dry-run and --verify-only. The auto-migrate hook is
fail-open so a broken migration never blocks server boot.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("pyarrow")
pytest.importorskip("lancedb")

from omniscribe.core.lexicon import LanceDBLexiconStore
from omniscribe.core.lexicon.embedding import EmbeddingModel
from omniscribe.core.lexicon.migration import (
    auto_migrate_if_needed,
    detect_legacy_state,
    run_migration,
)

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeEmbeddingModel:
    """Deterministic hash-based fake model (same one the store tests use)."""

    dim = 384
    model_name = "fake-test-model"

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        import hashlib

        out: list[list[float]] = []
        for t in texts:
            digest = hashlib.sha256(t.encode("utf-8")).digest()
            base = [b / 255.0 for b in digest] * 12
            vec = base[: self.dim]
            norm = sum(x * x for x in vec) ** 0.5 or 1.0
            vec = [x / norm for x in vec]
            out.append(vec)
        return out


@pytest.fixture
def fake_model() -> EmbeddingModel:
    return _FakeEmbeddingModel()


def _seed_legacy_library(artifact_dir: Path) -> Path:
    """Create a populated legacy ``library.json`` and return its path."""
    lib_dir = artifact_dir / "glossary_library"
    lib_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "glossaries": [
            {
                "id": "legal-1",
                "name": "Legal EN→FR",
                "format": "json_pairs",
                "source_uri": "inline",
                "encoding": "utf-8",
                "entries": [
                    {
                        "source": "agreement",
                        "target": "accord",
                        "case_sensitive": False,
                        "notes": "",
                    },
                    {
                        "source": "contract",
                        "target": "contrat",
                        "case_sensitive": False,
                        "notes": "",
                    },
                    {
                        "source": "liability",
                        "target": "responsabilité",
                        "case_sensitive": False,
                        "notes": "",
                    },
                ],
                "enabled": True,
                "priority": 10,
                "group": "legal",
                "created_at": 1000.0,
                "updated_at": 1000.0,
            },
            {
                "id": "tech-1",
                "name": "Tech",
                "format": "csv",
                "source_uri": "inline",
                "encoding": "utf-8",
                "entries": [
                    {
                        "source": "API",
                        "target": "API",
                        "case_sensitive": False,
                        "notes": "",
                    },
                    {
                        "source": "endpoint",
                        "target": "point d'accès",
                        "case_sensitive": False,
                        "notes": "",
                    },
                ],
                "enabled": True,
                "priority": 5,
                "group": "tech",
                "created_at": 1000.0,
                "updated_at": 1000.0,
            },
        ],
    }
    lib_path = lib_dir / "library.json"
    lib_path.write_text(json.dumps(payload), encoding="utf-8")
    return lib_path


# ---------------------------------------------------------------------------
# State detection
# ---------------------------------------------------------------------------


def test_detect_legacy_state_fresh(tmp_path: Path) -> None:
    state = detect_legacy_state(tmp_path)
    assert state["library_json"] is None
    assert state["chroma_db"] is None
    assert state["lexicon_lance"] is None


def test_detect_legacy_state_with_library_json(tmp_path: Path) -> None:
    _seed_legacy_library(tmp_path)
    state = detect_legacy_state(tmp_path)
    assert state["library_json"] is not None
    assert state["library_json"].name == "library.json"
    assert state["chroma_db"] is None
    assert state["lexicon_lance"] is None


def test_detect_legacy_state_with_existing_lance(tmp_path: Path) -> None:
    LanceDBLexiconStore(
        path=tmp_path / "lexicon.lance", embedding_model=_FakeEmbeddingModel()
    )
    state = detect_legacy_state(tmp_path)
    assert state["library_json"] is None
    assert state["lexicon_lance"] is not None


# ---------------------------------------------------------------------------
# Run migration
# ---------------------------------------------------------------------------


def test_run_migration_from_library_json(
    tmp_path: Path, fake_model: EmbeddingModel
) -> None:
    _seed_legacy_library(tmp_path)
    report = run_migration(artifact_dir=tmp_path, embedding_model=fake_model)
    assert report.ran is True
    assert report.error is None
    assert report.glossaries_migrated == 2
    assert report.entries_migrated == 5
    assert report.backup_dir is not None
    assert report.backup_dir.exists()

    # The new store has the data.
    store = LanceDBLexiconStore(
        path=tmp_path / "lexicon.lance", embedding_model=fake_model
    )
    metas = store.list_glossaries()
    assert len(metas) == 2
    by_name = {m.name: m for m in metas}
    assert "Legal EN→FR" in by_name
    assert "Tech" in by_name
    legal = by_name["Legal EN→FR"]
    assert legal.entry_count == 3
    assert legal.group == "legal"
    assert legal.priority == 10
    assert legal.source_uri == "inline"
    assert legal.encoding == "utf-8"

    # And the legacy file was moved into the backup.
    state = detect_legacy_state(tmp_path)
    assert state["library_json"] is None  # original location
    backup_lib = report.backup_dir / "library.json"
    assert backup_lib.exists()
    # The backup file is valid JSON with the same glossaries.
    payload = json.loads(backup_lib.read_text(encoding="utf-8"))
    assert len(payload["glossaries"]) == 2


def test_run_migration_dry_run(tmp_path: Path, fake_model: EmbeddingModel) -> None:
    _seed_legacy_library(tmp_path)
    report = run_migration(
        artifact_dir=tmp_path, embedding_model=fake_model, dry_run=True
    )
    assert report.ran is False
    assert report.dry_run is True
    assert report.glossaries_migrated == 2
    assert report.entries_migrated == 5
    assert report.backup_dir is None

    # Nothing was written or moved.
    state = detect_legacy_state(tmp_path)
    assert state["library_json"] is not None  # still in place
    assert state["lexicon_lance"] is None  # no new store


def test_run_migration_idempotent(tmp_path: Path, fake_model: EmbeddingModel) -> None:
    _seed_legacy_library(tmp_path)
    r1 = run_migration(artifact_dir=tmp_path, embedding_model=fake_model)
    assert r1.ran is True
    assert r1.backup_dir is not None
    assert r1.backup_dir.exists()

    # Second run: no legacy state, store already exists → no-op.
    r2 = run_migration(artifact_dir=tmp_path, embedding_model=fake_model)
    assert r2.ran is False
    assert r2.skipped is True
    assert "already present" in r2.skip_reason


def test_run_migration_refuses_ambiguous_state(
    tmp_path: Path, fake_model: EmbeddingModel
) -> None:
    _seed_legacy_library(tmp_path)
    LanceDBLexiconStore(path=tmp_path / "lexicon.lance", embedding_model=fake_model)
    report = run_migration(artifact_dir=tmp_path, embedding_model=fake_model)
    assert report.ran is False
    assert report.skipped is True
    assert "refusing to migrate" in report.skip_reason
    assert report.error is not None
    assert "Ambiguous state" in report.error


def test_run_migration_no_legacy_state_no_op(
    tmp_path: Path, fake_model: EmbeddingModel
) -> None:
    report = run_migration(artifact_dir=tmp_path, embedding_model=fake_model)
    assert report.ran is False
    assert report.skipped is True
    assert "no legacy state" in report.skip_reason


def test_run_migration_verify_only_no_lexicon(
    tmp_path: Path, fake_model: EmbeddingModel
) -> None:
    report = run_migration(
        artifact_dir=tmp_path, embedding_model=fake_model, verify_only=True
    )
    assert report.verified is True
    assert report.skipped is True


def test_run_migration_verify_only_with_existing(
    tmp_path: Path, fake_model: EmbeddingModel
) -> None:
    _seed_legacy_library(tmp_path)
    r1 = run_migration(artifact_dir=tmp_path, embedding_model=fake_model)
    assert r1.ran is True

    # Now verify.
    r2 = run_migration(
        artifact_dir=tmp_path, embedding_model=fake_model, verify_only=True
    )
    assert r2.verified is True
    assert r2.glossaries_migrated == 2
    assert r2.entries_migrated == 5
    assert r2.skipped is False


# ---------------------------------------------------------------------------
# Auto-migrate hook
# ---------------------------------------------------------------------------


def test_auto_migrate_if_needed_runs_when_legacy_present(
    tmp_path: Path, fake_model: EmbeddingModel
) -> None:
    _seed_legacy_library(tmp_path)
    report = auto_migrate_if_needed(tmp_path, embedding_model=fake_model)
    assert report.ran is True
    assert report.glossaries_migrated == 2


def test_auto_migrate_if_needed_noop_when_clean(
    tmp_path: Path, fake_model: EmbeddingModel
) -> None:
    report = auto_migrate_if_needed(tmp_path, embedding_model=fake_model)
    assert report.ran is False
    assert report.skipped is True


def test_auto_migrate_if_needed_fail_open(tmp_path: Path) -> None:
    """If the migration throws, the auto-migrate hook swallows it and
    returns a report with `error` set — the server can still boot."""
    _seed_legacy_library(tmp_path)

    # Pass a broken embedding model to force a failure.
    class _Broken:
        dim = 384
        model_name = "broken"

        def embed(self, text: str) -> list[float]:
            raise RuntimeError("nope")

        def embed_batch(self, texts: list[str]) -> list[list[float]]:
            raise RuntimeError("nope")

    report = auto_migrate_if_needed(tmp_path, embedding_model=_Broken())
    assert report.error is not None
    # Should NOT raise — the hook is fail-open.


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------


def test_cli_main_dry_run(
    tmp_path: Path, fake_model: EmbeddingModel, monkeypatch, capsys
) -> None:
    """The CLI should accept --dry-run and --artifact-dir."""
    from omniscribe.cli.migrate_lexicon import main as cli_main

    monkeypatch.setattr(
        "omniscribe.core.lexicon.migration.get_default_embedding_model",
        lambda: fake_model,
    )
    _seed_legacy_library(tmp_path)
    rc = cli_main(
        [
            "--dry-run",
            "--artifact-dir",
            str(tmp_path),
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "glossaries: 2" in out


def test_cli_main_verify_only(
    tmp_path: Path, fake_model: EmbeddingModel, monkeypatch, capsys
) -> None:
    from omniscribe.cli.migrate_lexicon import main as cli_main

    monkeypatch.setattr(
        "omniscribe.core.lexicon.migration.get_default_embedding_model",
        lambda: fake_model,
    )
    _seed_legacy_library(tmp_path)
    # First migrate for real.
    rc1 = cli_main(["--artifact-dir", str(tmp_path)])
    assert rc1 == 0
    capsys.readouterr()
    # Then verify.
    rc = cli_main(["--verify-only", "--artifact-dir", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "verify-only OK" in out
    assert "glossaries: 2" in out


def test_cli_main_refuses_ambiguous(
    tmp_path: Path, fake_model: EmbeddingModel, monkeypatch, capsys
) -> None:
    from omniscribe.cli.migrate_lexicon import main as cli_main

    monkeypatch.setattr(
        "omniscribe.core.lexicon.migration.get_default_embedding_model",
        lambda: fake_model,
    )
    _seed_legacy_library(tmp_path)
    LanceDBLexiconStore(path=tmp_path / "lexicon.lance", embedding_model=fake_model)
    rc = cli_main(["--artifact-dir", str(tmp_path)])
    assert rc == 1
    out = capsys.readouterr().out
    assert "Ambiguous state" in out


def test_cli_main_dry_run_and_verify_only_conflict(capsys) -> None:
    """--dry-run and --verify-only are mutually exclusive."""
    from omniscribe.cli.migrate_lexicon import main as cli_main

    rc = cli_main(["--dry-run", "--verify-only"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "mutually exclusive" in out


# ---------------------------------------------------------------------------
# Migration idempotency (LLM-remediation wave)
# ---------------------------------------------------------------------------


def test_migration_rerun_refused_then_ids_preserved(
    tmp_path: Path, fake_model: EmbeddingModel
) -> None:
    """Dirty-retry semantics: the ambiguity guard refuses a rerun while the
    legacy library coexists with lexicon.lance; once the operator resolves
    the ambiguity (lexicon removed, legacy is source of truth) the re-run
    preserves the original glossary ids instead of minting new ones.
    """
    artifact_dir = tmp_path / "artifacts"
    _seed_legacy_library(artifact_dir)
    report1 = run_migration(artifact_dir=artifact_dir, embedding_model=fake_model)
    assert report1.ran and report1.error is None

    # Simulate a mid-run-crash retry: legacy state present again.
    _seed_legacy_library(artifact_dir)
    report2 = run_migration(artifact_dir=artifact_dir, embedding_model=fake_model)
    assert not report2.ran
    assert report2.error is not None
    assert "coexists" in (report2.error or "")

    # Operator resolution: legacy is source of truth; drop the lexicon.
    import shutil

    shutil.rmtree(artifact_dir / "lexicon.lance")
    report3 = run_migration(artifact_dir=artifact_dir, embedding_model=fake_model)
    assert report3.ran and report3.error is None

    store = LanceDBLexiconStore(
        path=artifact_dir / "lexicon.lance", embedding_model=fake_model
    )
    metas = store.list_glossaries()
    assert sorted(m.id for m in metas) == ["legal-1", "tech-1"]
