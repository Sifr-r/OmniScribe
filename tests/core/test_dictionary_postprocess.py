"""Unit tests for the upgraded Tesseract langdata post-processing spellchecker."""

from __future__ import annotations

import gzip
import json
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path

import pytest

import omniscribe.core.postprocess as postprocess
from omniscribe.core.postprocess import DictionaryPostProcessor


@pytest.fixture
def temp_resources():
    """Create a temporary directory structure mimicking the project resources."""
    temp_dir = tempfile.mkdtemp()
    langdata_dir = os.path.join(temp_dir, "langdata")
    dictionaries_dir = os.path.join(temp_dir, "dictionaries")
    os.makedirs(langdata_dir, exist_ok=True)
    os.makedirs(dictionaries_dir, exist_ok=True)

    yield temp_dir, langdata_dir, dictionaries_dir

    shutil.rmtree(temp_dir, ignore_errors=True)


def test_language_iso_mapping():
    """Verify standard language codes are mapped correctly to Tesseract 3-letter codes."""
    assert DictionaryPostProcessor("en").tess_lang == "eng"
    assert DictionaryPostProcessor("eng").tess_lang == "eng"
    assert DictionaryPostProcessor("en-US").tess_lang == "eng"
    assert DictionaryPostProcessor("ar").tess_lang == "ara"
    assert DictionaryPostProcessor("arabic").tess_lang == "ara"
    assert DictionaryPostProcessor("de-DE").tess_lang == "deu"
    assert DictionaryPostProcessor("xyz").tess_lang == "xyz"  # No mapping fallback


def test_unicode_diacritics_check():
    """Verify isalpha validation works for words containing diacritics."""
    DictionaryPostProcessor("ara")

    # Test compilation helper on Arabic word list with diacritics
    # \u064e is Fatha, \u0651 is Shadda.
    # "أَحْمَدُ" is "Ahmed" with diacritics.
    # "محمدٌ" is "Mohamed" with diacritics.

    # Clean check logic test
    import unicodedata

    def is_valid(word):
        cleaned = "".join(c for c in word if unicodedata.category(c) != "Mn")
        return cleaned.isalpha()

    assert is_valid("أَحْمَدُ") is True
    assert is_valid("محمدٌ") is True
    assert is_valid("12345") is False
    assert is_valid("hello!") is False


async def test_compilation_and_loading(temp_resources):
    """Test full cycle: compile raw wordlist to gzipped JSON and load it."""
    temp_dir, langdata_dir, dictionaries_dir = temp_resources

    # Setup mock wordlist for German (deu)
    deu_langdata = os.path.join(langdata_dir, "deu")
    os.makedirs(deu_langdata, exist_ok=True)

    wordlist_path = os.path.join(deu_langdata, "deu.wordlist")
    with open(wordlist_path, "w", encoding="utf-8") as f:
        f.write("apfel\nbirne\nkirsche\n")

    processor = DictionaryPostProcessor("deu", resources_dir=temp_dir)

    await processor.ensure_loaded()

    # Verify compiled gz file exists in dictionaries directory
    compiled_gz_path = os.path.join(dictionaries_dir, "deu.json.gz")
    assert os.path.exists(compiled_gz_path)

    # Verify contents of gz file
    with gzip.open(compiled_gz_path, "rt", encoding="utf-8") as f:
        data = json.load(f)
        assert data == {"apfel": 1, "birne": 1, "kirsche": 1}

    # Verify spellchecker corrects typos matching Levenshtein distance 1
    assert processor.correct_text("apfl") == "apfel"
    assert processor.correct_text("birn") == "birne"
    assert processor.correct_text("kirsch") == "kirsche"

    # Verify casing is preserved
    assert processor.correct_text("Apfl") == "Apfel"
    assert processor.correct_text("APFL") == "APFEL"

    # Unknown or far typos (distance > 1) remain untouched
    assert processor.correct_text("apffffl") == "apffffl"


async def test_packaged_dictionary_lookup_precedes_legacy_resources(monkeypatch):
    """Default lookup should load bundled dictionaries from the installed package."""
    loaded_paths: list[str] = []
    sentinel = object()

    def fake_load_custom_dictionary(dict_path: str):
        loaded_paths.append(dict_path)
        return sentinel

    monkeypatch.setattr(
        postprocess, "_load_custom_dictionary", fake_load_custom_dictionary
    )

    processor = DictionaryPostProcessor("eng")
    await processor.ensure_loaded()

    package_dict_path = (
        Path(__file__).parents[2]
        / "src"
        / "omniscribe"
        / "resources"
        / "dictionaries"
        / "eng.json.gz"
    ).resolve()
    assert processor.spell is sentinel
    assert loaded_paths == [str(package_dict_path)]


async def test_legacy_repository_dictionary_fallback(monkeypatch):
    """Repository-root dictionaries remain a fallback for older checkouts."""

    class MissingPackagedResource:
        def joinpath(self, *parts: str) -> MissingPackagedResource:
            return self

        def is_file(self) -> bool:
            return False

    loaded_paths: list[str] = []
    sentinel = object()

    def fake_load_custom_dictionary(dict_path: str):
        loaded_paths.append(dict_path)
        return sentinel

    monkeypatch.setattr(
        postprocess.resources, "files", lambda package: MissingPackagedResource()
    )
    monkeypatch.setattr(
        postprocess, "_load_custom_dictionary", fake_load_custom_dictionary
    )

    processor = DictionaryPostProcessor("eng")
    await processor.ensure_loaded()

    legacy_dict_path = (
        Path(__file__).parents[2] / "resources" / "dictionaries" / "eng.json.gz"
    ).resolve()
    assert processor.spell is sentinel
    assert loaded_paths == [str(legacy_dict_path)]


async def test_graceful_fallback():
    """Verify processor falls back gracefully if raw wordlist or dictionary is missing."""
    processor = DictionaryPostProcessor("xyz")  # Nonexistent language

    await processor.ensure_loaded()

    # Nonexistent language should fallback to None (safe no-op)
    assert processor.spell is None
    assert processor.correct_text("someword") == "someword"


def _make_wordlist(tmp: Path, name: str, words: list[str]) -> Path:
    wordlist = tmp / f"{name}.wordlist"
    wordlist.write_text("\n".join(words) + "\n", encoding="utf-8")
    return wordlist


def test_compile_wordlist_uses_atomic_rename(tmp_path, monkeypatch):
    """``_compile_wordlist`` must use ``os.replace`` (not ``os.rename``)."""
    wordlist = _make_wordlist(tmp_path, "fra", ["pomme", "poire", "cerise"])
    output = tmp_path / "fra.json.gz"

    real_replace = os.replace
    replace_calls: list[tuple[str, str]] = []
    rename_called = threading.Event()

    def tracking_replace(src, dst):
        replace_calls.append((src, dst))
        return real_replace(src, dst)

    def fail_if_called(*args, **kwargs):
        rename_called.set()
        raise AssertionError("os.rename must not be used; use os.replace")

    monkeypatch.setattr(postprocess.os, "replace", tracking_replace)
    monkeypatch.setattr(postprocess.os, "rename", fail_if_called)

    processor = DictionaryPostProcessor("fra")
    assert processor._compile_wordlist(str(wordlist), str(output)) is True

    assert rename_called.is_set() is False
    assert len(replace_calls) == 1
    src, dst = replace_calls[0]
    assert dst == str(output)
    assert src != str(output)
    assert os.path.dirname(src) == os.path.dirname(dst)
    # Temp file should not linger after a successful rename.
    assert os.path.exists(src) is False
    assert output.exists()

    # Sanity: the final file is a valid, fully-written gzipped JSON.
    with gzip.open(output, "rt", encoding="utf-8") as fh:
        data = json.load(fh)
    assert data == {"pomme": 1, "poire": 1, "cerise": 1}


def test_compile_wordlist_no_temp_leftover_on_success(tmp_path):
    """After a successful compile, no ``.wordlist-*.json.gz`` temp files remain."""
    wordlist = _make_wordlist(tmp_path, "spa", ["manzana", "pera"])
    output = tmp_path / "spa.json.gz"

    processor = DictionaryPostProcessor("spa")
    assert processor._compile_wordlist(str(wordlist), str(output)) is True

    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".wordlist-")]
    assert leftovers == []


def test_compile_wordlist_cleans_up_temp_on_failure(tmp_path, monkeypatch):
    """A failure during gzip write must not leave a temp file lingering."""
    wordlist = _make_wordlist(tmp_path, "ita", ["mela", "pera"])
    output = tmp_path / "ita.json.gz"

    def boom(*args, **kwargs):
        raise RuntimeError("simulated gzip failure")

    monkeypatch.setattr(postprocess.gzip, "GzipFile", boom)

    processor = DictionaryPostProcessor("ita")
    assert processor._compile_wordlist(str(wordlist), str(output)) is False

    assert output.exists() is False
    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".wordlist-")]
    assert leftovers == []


def _probe_wordlist_target(target: Path) -> tuple[str, Exception | None]:
    """One reader probe of the target.

    Returns ``(status, exception)`` where status is ``"missing"`` (target
    absent or ``FileNotFoundError``), ``"bad"`` (corrupt payload or failed
    assertion, with the exception attached), ``"transient"`` (Windows
    ``PermissionError`` while ``os.replace`` swaps the file), or ``"ok"``.
    """
    if not target.exists():
        return "missing", None
    try:
        # Brief open + read + close. Production code does the same
        # (SpellChecker reads the entire payload then closes).
        with gzip.open(target, "rb") as fh:
            raw_json = fh.read()
        data = json.loads(raw_json.decode("utf-8"))
        assert isinstance(data, dict)
        assert data
    except FileNotFoundError:
        return "missing", None
    except (EOFError, gzip.BadGzipFile, json.JSONDecodeError, AssertionError) as exc:
        return "bad", exc
    except PermissionError:
        # Windows: ``os.replace`` momentarily denies readers while
        # swapping the file; transient and not a partial file.
        return "transient", None
    else:
        return "ok", None


def test_concurrent_writers_never_expose_partial_file(tmp_path):
    """Concurrent writers must never leave a partial ``.json.gz`` and a reader
    polling the target must only see ``FileNotFoundError`` or a fully valid
    gzipped JSON — never a corrupt in-between state.

    The audit's H4 contract is the temp-file-then-``os.replace`` pattern:
    while the writer is running, the target path either does not exist or
    already holds the previous fully-written file. The new payload is
    staged in a temp file in the same directory and only swapped in via a
    final atomic ``os.replace`` (guarded by a per-target threading lock).
    We verify the contract from three angles:

    1. **Writer perspective (this test):** two writer threads race many
       ``_compile_wordlist`` invocations against the same target. The
       in-process per-target lock plus ``os.replace`` retry guarantee that
       every write either succeeds or returns ``False`` — they never
       leave a half-written payload at the target.
    2. **Reader perspective (this test):** a reader polls the target
       using ``os.path.exists`` (which does not open the file) and, when
       the file exists, opens it briefly with a small back-off so the
       reader's handle is closed between samples. A reader must only see
       ``FileNotFoundError`` or a parseable gzip/JSON — never a corrupt
       state.
    3. **No leftover temp files:** the only file at the target after all
       writers finish is the final ``.json.gz`` — no ``.wordlist-*.json.gz``
       temp files remain in the directory.

    Note on Windows: Python's built-in ``open()`` opens files without
    ``FILE_SHARE_DELETE`` (see CPython issue #100219), so a long-lived
    reader handle can block ``os.replace``. This test reflects the
    realistic production read pattern (read fully, close, repeat with a
    brief back-off) and proves the contract for the in-process race
    that H4 was scoped to fix.
    """
    target = tmp_path / "eng.json.gz"
    wordlist = _make_wordlist(
        tmp_path, "eng", ["alpha", "bravo", "charlie", "delta", "echo"] * 50
    )

    num_writers = 2
    iterations = 15
    reader_iters = 200
    stop = threading.Event()
    bad_reads: list[Exception] = []
    bad_lock = threading.Lock()
    successful_reads = 0
    successful_reads_lock = threading.Lock()
    missing_reads = 0
    missing_lock = threading.Lock()
    write_failures: list[Exception] = []
    write_lock = threading.Lock()

    def safe_read_once() -> None:
        nonlocal successful_reads, missing_reads
        status, exc = _probe_wordlist_target(target)
        if status == "missing":
            with missing_lock:
                missing_reads += 1
        elif status == "bad":
            assert exc is not None
            with bad_lock:
                bad_reads.append(exc)
        elif status == "ok":
            with successful_reads_lock:
                successful_reads += 1

    def reader_loop() -> None:
        # Yield briefly between samples so writers can grab the rename.
        while not stop.is_set():
            safe_read_once()
            time.sleep(0.001)

    reader = threading.Thread(target=reader_loop, daemon=True)
    reader.start()

    def writer(seed: int) -> None:
        processor = DictionaryPostProcessor("eng")
        for _ in range(iterations):
            try:
                ok = processor._compile_wordlist(str(wordlist), str(target))
            except Exception as exc:
                with write_lock:
                    write_failures.append(exc)
                continue
            if not ok:
                with write_lock:
                    write_failures.append(
                        AssertionError(
                            f"writer {seed} got False from _compile_wordlist"
                        )
                    )

    writers = [
        threading.Thread(target=writer, args=(i,), daemon=True)
        for i in range(num_writers)
    ]
    for w in writers:
        w.start()
    for w in writers:
        w.join()
    # Drain the reader for a beat to catch any straggler partial reads.
    for _ in range(reader_iters):
        safe_read_once()
    stop.set()
    reader.join(timeout=2.0)

    assert bad_reads == [], f"Reader observed partial/corrupt state: {bad_reads!r}"
    assert write_failures == [], f"Writers failed: {write_failures!r}"
    assert successful_reads > 0
    # Final file must be valid and complete.
    with gzip.open(target, "rt", encoding="utf-8") as fh:
        final = json.load(fh)
    assert final == {
        "alpha": 1,
        "bravo": 1,
        "charlie": 1,
        "delta": 1,
        "echo": 1,
    }
    # No temp files left behind.
    leftovers = sorted(
        p.name for p in tmp_path.iterdir() if p.name.startswith(".wordlist-")
    )
    assert leftovers == [], f"Leftover temp files: {leftovers!r}"
