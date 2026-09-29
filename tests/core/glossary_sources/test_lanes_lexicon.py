"""Tests for the Lane's Arabic-English Lexicon glossary parsers.

The fixtures here are intentionally tiny (two minimal TEI entries and
a 3-row SQLite snapshot) — the design goal is to lock the parser's
shape and security guarantees, not to re-test the 264 MB real database
that already lives at ``D:/Lanes/lexicon.sqlite``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from omniscribe.core.glossary_sources import (
    parse,
    parse_lanes_lexicon_sqlite,
    parse_lanes_lexicon_xml,
)
from omniscribe.core.translate.glossary import Glossary

# ---------------------------------------------------------------------------
# SQLite fixture builder (audit P3-11 style: ephemeral tmp_path DB)
# ---------------------------------------------------------------------------


def _build_lane_sqlite_fixture(path: Path) -> Path:
    """Write a 3-row Lane-shaped SQLite snapshot to ``path``.

    Mirrors the real ``laneslexicon/LexiconDatabase`` schema (entry +
    pos + root tables; entry.xml column carrying the embedded TEI).
    """
    db_path = path / "lanes_mini.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE entry (
                id INTEGER PRIMARY KEY,
                nodeid TEXT NOT NULL,
                root TEXT,
                word TEXT NOT NULL,
                bword TEXT,
                itype TEXT,
                page INTEGER,
                xml TEXT,
                type TEXT,
                status TEXT
            );
            CREATE TABLE pos (nodeid TEXT NOT NULL, pos TEXT NOT NULL);
            CREATE TABLE root (root TEXT NOT NULL);
            """
        )
        # node n1 — a verb form, with a ``<hi rend="ital">A shower,</hi>``
        # gloss followed by a long definition.
        entry_xml = (
            '<entryFree id="n1" key="أَبَّ"><form>'
            '<orth lang="ar">أَبَّ</orth><itype>verb</itype></form>'
            '<hi rend="ital">A shower,</hi> or <hi rend="ital">fall.</hi> '
            "It rained very hard. See Lane page 5.</entryFree>"
        )
        # Buckwalter transliteration uses a modifier-letter right half
        # ring (Unicode U+02BE) for the glottal stop; ruff flags it as
        # ambiguous against the ASCII apostrophe, but the spelling is
        # correct in the Lane dataset — use the Unicode escape so the
        # linter doesn't see the bare character.
        conn.execute(
            "INSERT INTO entry (id, nodeid, word, bword, itype, page, xml) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (1, "n1", "أَبَّ", "\u02beabba", "verb", 5, entry_xml),
        )
        conn.execute("INSERT INTO pos VALUES ('n1', 'verb')")
        # node n2 — noun with the standard "Mighty, potent, powerful" form.
        entry_xml2 = (
            '<entryFree id="n2" key="عَزِيز"><form>'
            '<orth lang="ar">عَزِيز</orth></form>'
            '<hi rend="ital">Mighty, potent, powerful,</hi> or strong. '
            "Said of God. See Lane page 219.</entryFree>"
        )
        conn.execute(
            "INSERT INTO entry (id, nodeid, word, bword, page, xml) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (2, "n2", "عَزِيز", "ʿazīz", 219, entry_xml2),
        )
        # node n3 — empty word, must be filtered out.
        conn.execute(
            "INSERT INTO entry (id, nodeid, word, xml) VALUES (?, ?, ?, ?)",
            (3, "n3", "", "<entryFree/>"),
        )
    return db_path


# ---------------------------------------------------------------------------
# XML fixture builder
# ---------------------------------------------------------------------------


LANE_TEI_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<TEI.2 xmlns="http://www.tei-c.org/ns/1.0">
  <text>
    <body>
      <div>
        <entryFree id="n1" key="أَبَّ">
          <form>
            <orth lang="ar">أَبَّ</orth>
            <itype>verb</itype>
          </form>
          <hi rend="ital">A shower,</hi> or <hi rend="ital">fall.</hi>
          It rained very hard. See Lane page 5.
        </entryFree>
        <entryFree id="n2" key="عَزِيز">
          <form>
            <orth lang="ar">عَزِيز</orth>
          </form>
          <hi rend="ital">Mighty, potent, powerful,</hi> or strong.
          Said of God. See Lane page 219.
        </entryFree>
      </div>
    </body>
  </text>
</TEI.2>
"""


def _write_xml_fixture(tmp_path: Path) -> Path:
    xml_path = tmp_path / "lane_mini.xml"
    xml_path.write_text(LANE_TEI_FIXTURE, encoding="utf-8")
    return xml_path


# ---------------------------------------------------------------------------
# lanes_sqlite
# ---------------------------------------------------------------------------


class TestLanesSqlite:
    def test_parses_real_sqlite_shape(self, tmp_path: Path) -> None:
        db_path = _build_lane_sqlite_fixture(tmp_path)
        summary = parse_lanes_lexicon_sqlite(db_path=str(db_path))
        assert summary.format == "lanes_sqlite"
        assert summary.source_uri == db_path.as_posix()
        # The empty-word row must be skipped — only 2 entries.
        assert len(summary.entries) == 2
        by_source = {entry["source"]: entry for entry in summary.entries}
        assert "أَبَّ" in by_source
        assert "عَزِيز" in by_source

    def test_first_gloss_is_concise_target(self, tmp_path: Path) -> None:
        db_path = _build_lane_sqlite_fixture(tmp_path)
        summary = parse_lanes_lexicon_sqlite(db_path=str(db_path))
        by_source = {entry["source"]: entry for entry in summary.entries}
        # First <hi rend="ital">``A shower,`` truncated at first comma.
        assert by_source["أَبَّ"]["target"] == "A shower"
        # First <hi rend="ital">``Mighty, potent, powerful,`` → before first comma.
        assert by_source["عَزِيز"]["target"] == "Mighty"

    def test_notes_contain_full_definition(self, tmp_path: Path) -> None:
        db_path = _build_lane_sqlite_fixture(tmp_path)
        summary = parse_lanes_lexicon_sqlite(db_path=str(db_path))
        notes = summary.entries[0]["notes"]
        assert isinstance(notes, str)
        assert notes.startswith("Lane's Arabic-English Lexicon")
        assert "Gloss: A shower" in notes
        assert "Buckwalter: \u02beabba" in notes
        assert "Lane page: 5" in notes
        # Full Lane definition must be present.
        assert "It rained very hard" in notes

    def test_pos_join_lands_in_entry(self, tmp_path: Path) -> None:
        db_path = _build_lane_sqlite_fixture(tmp_path)
        summary = parse_lanes_lexicon_sqlite(db_path=str(db_path))
        by_source = {entry["source"]: entry for entry in summary.entries}
        assert by_source["أَبَّ"]["pos"] == "verb"
        assert by_source["أَبَّ"]["register"] == "verb"
        assert by_source["عَزِيز"]["register"] is None  # no itype on this row

    def test_case_sensitive_and_languages(self, tmp_path: Path) -> None:
        db_path = _build_lane_sqlite_fixture(tmp_path)
        summary = parse_lanes_lexicon_sqlite(db_path=str(db_path))
        entry = summary.entries[0]
        assert entry["case_sensitive"] is True
        assert entry["source_lang"] == "ara"
        assert entry["target_lang"] == "eng"
        assert entry["domain"] == "Lane's Lexicon"

    def test_limit_caps_results(self, tmp_path: Path) -> None:
        db_path = _build_lane_sqlite_fixture(tmp_path)
        summary = parse_lanes_lexicon_sqlite(db_path=str(db_path), limit=1)
        assert len(summary.entries) == 1

    def test_dispatches_through_parse(self, tmp_path: Path) -> None:
        db_path = _build_lane_sqlite_fixture(tmp_path)
        summary = parse(format="lanes_sqlite", db_path=str(db_path))
        assert summary.format == "lanes_sqlite"
        assert summary.entries

    def test_round_trips_through_glossary(self, tmp_path: Path) -> None:
        """Lane entries must flow through ``Glossary.from_dict`` like
        any other source — the parser outputs the same IR shape."""
        db_path = _build_lane_sqlite_fixture(tmp_path)
        summary = parse_lanes_lexicon_sqlite(db_path=str(db_path))
        glossary = Glossary.from_dict({"entries": summary.entries})
        assert len(glossary.entries) == len(summary.entries)
        for entry in glossary.entries:
            assert entry.source
            assert entry.target
            assert entry.case_sensitive is True
            # ``notes`` round-trip is the crucial signal — Lane entries
            # would lose the long definition if the IR shape didn't
            # carry it through.
            assert "Lane's Arabic-English Lexicon" in entry.notes

    # ---- negative tests -----------------------------------------------------

    def test_rejects_url(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="local filesystem path"):
            parse_lanes_lexicon_sqlite(db_path="https://example.com/lexicon.sqlite")

    def test_rejects_empty_path(self) -> None:
        with pytest.raises(ValueError, match="required"):
            parse_lanes_lexicon_sqlite(db_path="")

    def test_rejects_nonexistent_path(self) -> None:
        with pytest.raises(ValueError, match="does not exist"):
            parse_lanes_lexicon_sqlite(db_path="D:/no/such/file.sqlite")

    def test_rejects_env_var_path(self) -> None:
        with pytest.raises(ValueError, match="invalid characters"):
            parse_lanes_lexicon_sqlite(db_path="%TEMP%/lexicon.sqlite")

    def test_rejects_directory_for_sqlite(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="must point at a file"):
            parse_lanes_lexicon_sqlite(db_path=str(tmp_path))

    @pytest.mark.parametrize("bad_limit", [0, -1, 1_000_001])
    def test_limit_bounds(self, tmp_path: Path, bad_limit: int) -> None:
        db_path = _build_lane_sqlite_fixture(tmp_path)
        with pytest.raises(ValueError):
            parse_lanes_lexicon_sqlite(db_path=str(db_path), limit=bad_limit)

    def test_max_entries_dispatch_passes_through(self, tmp_path: Path) -> None:
        """``parse(..., max_entries=1)`` should forward to ``limit=1``."""
        db_path = _build_lane_sqlite_fixture(tmp_path)
        summary = parse(format="lanes_sqlite", db_path=str(db_path), max_entries=1)
        assert len(summary.entries) == 1


# ---------------------------------------------------------------------------
# lanes_xml
# ---------------------------------------------------------------------------


class TestLanesXml:
    def test_parses_single_file(self, tmp_path: Path) -> None:
        xml_path = _write_xml_fixture(tmp_path)
        summary = parse_lanes_lexicon_xml(xml_path=str(xml_path))
        assert summary.format == "lanes_xml"
        assert summary.source_uri == xml_path.as_posix()
        assert len(summary.entries) == 2
        by_source = {entry["source"]: entry for entry in summary.entries}
        assert "أَبَّ" in by_source
        assert "عَزِيز" in by_source

    def test_parses_directory_of_xml(self, tmp_path: Path) -> None:
        _write_xml_fixture(tmp_path)
        # Second file with one entry.
        second = (
            "<TEI.2 xmlns='http://www.tei-c.org/ns/1.0'>"
            "<text><body><entryFree id='n3' key='كَتَبَ'>"
            "<form><orth lang='ar'>كَتَبَ</orth></form>"
            "<hi rend='ital'>He wrote,</hi> or recorded.</entryFree>"
            "</body></text></TEI.2>"
        )
        (tmp_path / "b.xml").write_text(second, encoding="utf-8")
        summary = parse_lanes_lexicon_xml(xml_path=str(tmp_path))
        assert summary.format == "lanes_xml"
        assert len(summary.entries) == 3
        sources = {entry["source"] for entry in summary.entries}
        assert sources == {"أَبَّ", "عَزِيز", "كَتَبَ"}

    def test_first_gloss_extraction(self, tmp_path: Path) -> None:
        xml_path = _write_xml_fixture(tmp_path)
        summary = parse_lanes_lexicon_xml(xml_path=str(xml_path))
        by_source = {entry["source"]: entry for entry in summary.entries}
        assert by_source["أَبَّ"]["target"] == "A shower"
        assert by_source["عَزِيز"]["target"] == "Mighty"

    def test_dispatches_through_parse(self, tmp_path: Path) -> None:
        xml_path = _write_xml_fixture(tmp_path)
        summary = parse(format="lanes_xml", xml_path=str(xml_path))
        assert summary.format == "lanes_xml"
        assert summary.entries

    def test_limit_caps_results(self, tmp_path: Path) -> None:
        xml_path = _write_xml_fixture(tmp_path)
        summary = parse_lanes_lexicon_xml(xml_path=str(xml_path), limit=1)
        assert len(summary.entries) == 1

    # ---- negative tests -----------------------------------------------------

    def test_rejects_url(self) -> None:
        with pytest.raises(ValueError, match="local filesystem path"):
            parse_lanes_lexicon_xml(xml_path="https://example.com/lane.xml")

    def test_rejects_empty_directory(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match=r"no \.xml files"):
            parse_lanes_lexicon_xml(xml_path=str(tmp_path))

    def test_round_trips_through_glossary(self, tmp_path: Path) -> None:
        xml_path = _write_xml_fixture(tmp_path)
        summary = parse_lanes_lexicon_xml(xml_path=str(xml_path))
        glossary = Glossary.from_dict({"entries": summary.entries})
        assert len(glossary.entries) == 2
        for entry in glossary.entries:
            assert entry.case_sensitive is True
            assert "Lane's Arabic-English Lexicon" in entry.notes


# ---------------------------------------------------------------------------
# Dispatch integration
# ---------------------------------------------------------------------------


def test_parse_dispatches_unknown_format() -> None:
    with pytest.raises(ValueError, match="Unsupported glossary format"):
        parse(format="lanes_undefined", db_path="x")


def test_parse_dispatches_max_entries_for_csv() -> None:
    """``max_entries`` for lanes_* flows to the parser's ``limit``; for csv
    it raises ``GlossaryImportLimitError`` when the entry count exceeds it."""
    from omniscribe.core.glossary_sources import GlossaryImportLimitError

    with pytest.raises(GlossaryImportLimitError) as excinfo:
        parse(
            format="csv",
            data=b"source,target\nHello,Hola\nWorld,Mundo\n",
            max_entries=1,
        )
    assert excinfo.value.limit == 1


# ---------------------------------------------------------------------------
# HTTP route extension inference (lanes_sqlite + lanes_xml)
# ---------------------------------------------------------------------------


def test_route_inference_recognises_lanes_extensions() -> None:
    """[P1-8] ``EXTENSION_TO_FORMAT`` must NOT map ``.sqlite``/``.db`` and ``.xml``
    so that multipart/URL uploads do not infer parsers requiring local paths."""
    from omniscribe.plugins.glossary.routes import (
        EXTENSION_TO_FORMAT,
        _infer_format_from_name,
    )

    assert "sqlite" not in EXTENSION_TO_FORMAT
    assert "db" not in EXTENSION_TO_FORMAT
    assert "xml" not in EXTENSION_TO_FORMAT

    # Bare filenames and URL paths for these extensions return None
    assert _infer_format_from_name("lexicon.sqlite") is None
    assert _infer_format_from_name("lane.db") is None
    assert _infer_format_from_name("https://example.com/path/to/lane.xml") is None

    # Unknown extension still returns ``None`` so the caller surfaces
    # the inference-failure error envelope.
    assert _infer_format_from_name("lexicon.unknown") is None


def test_entry_text_fragments_in_order_and_nested_tails() -> None:
    from omniscribe.core.glossary_sources._common import safe_xml_root
    from omniscribe.core.glossary_sources.lanes_lexicon import _entry_text_fragments

    xml = (
        b'<entryFree id="n100">'
        b'<form><orth lang="ar">\xd8\xa3\xd9\x8e\xd8\xa8\xd9\x8e\xd9\x8e</orth><itype>verb</itype></form>'
        b'<p>Leading text <hi rend="ital">italic text</hi> middle text '
        b'<foreign lang="ar">arabic inline</foreign> trailing text.</p>'
        b"Root tail text."
        b"</entryFree>"
    )
    root = safe_xml_root(xml)
    fragments = list(_entry_text_fragments(root))
    assert fragments == [
        "verb",
        "Leading text",
        "italic text",
        "middle text",
        "arabic inline",
        "trailing text.",
        "Root tail text.",
    ]


def test_entry_headword_prioritizes_arabic() -> None:
    from omniscribe.core.glossary_sources._common import safe_xml_root
    from omniscribe.core.glossary_sources.lanes_lexicon import _entry_headword

    # Case 1: Buckwalter / romanized comes before Arabic orthography
    xml1 = (
        '<entryFree id="n101" key="fallback_key">'
        "<form>"
        "<orth>romanized_first</orth>"
        '<orth lang="ar">عَزِيز</orth>'
        "</form>"
        "</entryFree>"
    ).encode()
    root1 = safe_xml_root(xml1)
    assert _entry_headword(root1) == "عَزِيز"

    # Case 2: Only non-Arabic orthography
    xml2 = (
        b'<entryFree id="n102" key="fallback_key">'
        b"<form>"
        b"<orth>only_romanized</orth>"
        b"</form>"
        b"</entryFree>"
    )
    root2 = safe_xml_root(xml2)
    assert _entry_headword(root2) == "only_romanized"

    # Case 3: Empty form falls back to key attribute
    xml3 = b'<entryFree id="n103" key="key_headword"><form></form></entryFree>'
    root3 = safe_xml_root(xml3)
    assert _entry_headword(root3) == "key_headword"
