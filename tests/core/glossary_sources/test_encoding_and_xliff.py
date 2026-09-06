"""Comprehensive test suite for encoding auto-detection and XLIFF 1.2 / 2.0 parsers."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from omniscribe.core.glossary_sources.encoding import (
    detect_encoding,
    read_text_auto_detect,
)
from omniscribe.core.glossary_sources.xliff import parse_xliff


def _make_xliff_12(units: list[tuple[str | None, str | None]]) -> bytes:
    """Build a minimal XLIFF 1.2 byte document from (source, target) tuples."""
    body_parts: list[str] = []
    for idx, (src, tgt) in enumerate(units, start=1):
        tu_parts = [f'            <trans-unit id="tu_{idx}">']
        if src is not None:
            tu_parts.append(f"                <source>{src}</source>")
        if tgt is not None:
            tu_parts.append(f"                <target>{tgt}</target>")
        tu_parts.append("            </trans-unit>")
        body_parts.append("\n".join(tu_parts))

    xml_text = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<xliff version="1.2" xmlns="urn:oasis:names:tc:xliff:document:1.2">\n'
        '    <file original="test.txt" source-language="en" target-language="fr" datatype="plaintext">\n'
        "        <body>\n" + "\n".join(body_parts) + "\n        </body>\n"
        "    </file>\n"
        "</xliff>"
    )
    return xml_text.encode("utf-8")


def _make_xliff_20(units: list[tuple[str | None, str | None]]) -> bytes:
    """Build a minimal XLIFF 2.0 byte document from (source, target) tuples."""
    unit_parts: list[str] = []
    for idx, (src, tgt) in enumerate(units, start=1):
        u_parts = [f'        <unit id="u_{idx}">', "            <segment>"]
        if src is not None:
            u_parts.append(f"                <source>{src}</source>")
        if tgt is not None:
            u_parts.append(f"                <target>{tgt}</target>")
        u_parts.extend(["            </segment>", "        </unit>"])
        unit_parts.append("\n".join(u_parts))

    xml_text = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<xliff version="2.0" xmlns="urn:oasis:names:tc:xliff:document:2.0" srcLang="en" trgLang="fr">\n'
        '    <file id="f1">\n' + "\n".join(unit_parts) + "\n    </file>\n"
        "</xliff>"
    )
    return xml_text.encode("utf-8")


# ==============================================================================
# 1. read_text_auto_detect and BOM Detection
# ==============================================================================


class TestBOMAndAutoDetection:
    """Tests for BOM detection and read_text_auto_detect across UTF encodings."""

    def test_utf8_with_bom(self) -> None:
        raw = b"\xef\xbb\xbfHello world"
        enc, warn = detect_encoding(raw)
        assert enc == "utf-8-sig"
        assert warn == ""

        text, used = read_text_auto_detect(raw)
        assert used == "utf-8-sig"
        assert text == "Hello world"

    def test_utf8_without_bom(self) -> None:
        raw = "Hello world — café".encode()
        enc, warn = detect_encoding(raw)
        assert enc == "utf-8"
        assert warn == ""

        text, used = read_text_auto_detect(raw)
        assert used == "utf-8"
        assert text == "Hello world — café"

    def test_utf16_le_with_bom(self) -> None:
        payload = "Hello UTF-16LE World".encode("utf-16-le")
        raw = b"\xff\xfe" + payload
        enc, warn = detect_encoding(raw)
        assert enc == "utf-16-le"
        assert warn == ""

        text, used = read_text_auto_detect(raw)
        assert used == "utf-16-le"
        assert text == "Hello UTF-16LE World"

    def test_utf16_be_with_bom(self) -> None:
        payload = "Hello UTF-16BE World".encode("utf-16-be")
        raw = b"\xfe\xff" + payload
        enc, warn = detect_encoding(raw)
        assert enc == "utf-16-be"
        assert warn == ""

        text, used = read_text_auto_detect(raw)
        assert used == "utf-16-be"
        assert text == "Hello UTF-16BE World"

    def test_utf32_le_with_bom(self) -> None:
        payload = "Hello UTF-32LE World".encode("utf-32-le")
        raw = b"\xff\xfe\x00\x00" + payload
        enc, warn = detect_encoding(raw)
        assert enc == "utf-32-le"
        assert warn == ""

        text, used = read_text_auto_detect(raw)
        assert used == "utf-32-le"
        assert text == "Hello UTF-32LE World"

    def test_utf32_be_with_bom(self) -> None:
        payload = "Hello UTF-32BE World".encode("utf-32-be")
        raw = b"\x00\x00\xfe\xff" + payload
        enc, warn = detect_encoding(raw)
        assert enc == "utf-32-be"
        assert warn == ""

        text, used = read_text_auto_detect(raw)
        assert used == "utf-32-be"
        assert text == "Hello UTF-32BE World"

    def test_utf32_le_precedence_over_utf16_le(self) -> None:
        """4-byte BOM b'\\xff\\xfe\\x00\\x00' must not be misidentified as 2-byte UTF-16LE."""
        raw = b"\xff\xfe\x00\x00" + "A".encode("utf-32-le")
        enc, _ = detect_encoding(raw)
        assert enc == "utf-32-le"

    def test_bytearray_input_supported(self) -> None:
        raw = bytearray(b"\xef\xbb\xbfBytearray content")
        text, used = read_text_auto_detect(raw)  # type: ignore[arg-type]
        assert used == "utf-8-sig"
        assert text == "Bytearray content"

    def test_explicit_encoding_override(self) -> None:
        raw = b"Explicit encoding test"
        text, used = read_text_auto_detect(raw, encoding="utf-8")
        assert used == "utf-8"
        assert text == "Explicit encoding test"


# ==============================================================================
# 2. Fallback Encoding Detection (Windows-1252, ISO-8859-1)
# ==============================================================================


class TestFallbackEncoding:
    """Tests for fallback encoding resolution when content is non-UTF-8."""

    def test_fallback_windows_1252_detection(self) -> None:
        # 0x80 is the Euro sign (€) in Windows-1252, which is an invalid standalone UTF-8 byte
        raw = "Coût: 100 € — payé".encode("windows-1252")
        text, used = read_text_auto_detect(raw)
        assert used.lower() in ("windows-1252", "cp1252")
        assert "100 €" in text

    def test_fallback_windows_1252_when_chardet_unavailable(self) -> None:
        raw = b"Prix: 50 \x80"  # 0x80 is valid in Windows-1252 (€)
        with patch.dict("sys.modules", {"chardet": None}):
            enc, warn = detect_encoding(raw)
            assert enc == "windows-1252"
            assert "windows-1252" in warn

            text, used = read_text_auto_detect(raw)
            assert used == "windows-1252"
            assert text == "Prix: 50 €"

    def test_fallback_iso_8859_1_detection(self) -> None:
        # 0x81 is undefined in Windows-1252, but valid in ISO-8859-1
        raw = b"Control code: \x81\x8d"
        enc, warn = detect_encoding(raw)
        assert enc.lower() in ("iso-8859-1", "latin-1")
        assert "iso-8859-1" in warn or "latin-1" in warn

        text, used = read_text_auto_detect(raw)
        assert used.lower() in ("iso-8859-1", "latin-1")
        assert text == "Control code: \x81\x8d"

    def test_fallback_iso_8859_1_when_chardet_unavailable(self) -> None:
        raw = b"Header: \x81"  # Undefined in CP1252, falls back to ISO-8859-1
        with patch.dict("sys.modules", {"chardet": None}):
            enc, warn = detect_encoding(raw)
            assert enc == "iso-8859-1"
            assert "iso-8859-1" in warn

            text, used = read_text_auto_detect(raw)
            assert used == "iso-8859-1"
            assert text == "Header: \x81"

    def test_chardet_low_confidence_falls_back_to_windows_1252(self) -> None:
        raw = b"Article \x96 Section"  # 0x96 is en-dash in Windows-1252
        mock_chardet = type(
            "ChardetMock",
            (),
            {"detect": lambda data: {"encoding": "ASCII", "confidence": 0.2}},
        )
        with patch.dict("sys.modules", {"chardet": mock_chardet}):
            enc, _ = detect_encoding(raw)
            assert enc == "windows-1252"


# ==============================================================================
# 3. Malformed / Undecodable Bytes Handling
# ==============================================================================


class TestMalformedBytesHandling:
    """Tests for error handling when invalid or malformed data is provided."""

    def test_non_bytes_input_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="bytes"):
            read_text_auto_detect("string is not bytes")  # type: ignore[arg-type]

        with pytest.raises(ValueError, match="bytes"):
            read_text_auto_detect(12345)  # type: ignore[arg-type]

        with pytest.raises(ValueError, match="bytes"):
            read_text_auto_detect(None)  # type: ignore[arg-type]

        with pytest.raises(ValueError, match="bytes"):
            read_text_auto_detect([1, 2, 3])  # type: ignore[arg-type]

    def test_unknown_explicit_encoding_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown encoding"):
            read_text_auto_detect(b"valid content", encoding="invalid_encoding_xyz")

    def test_undecodable_bytes_with_explicit_encoding_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Could not decode"):
            read_text_auto_detect(b"\xff\xff\xff", encoding="utf-8")

    def test_truncated_utf16_bom_raises_value_error(self) -> None:
        # Odd number of bytes after UTF-16LE BOM
        raw = b"\xff\xfe\x00"
        with pytest.raises(ValueError, match="Could not decode"):
            read_text_auto_detect(raw)

    def test_truncated_utf32_bom_raises_value_error(self) -> None:
        # Non-multiple of 4 bytes after UTF-32BE BOM
        raw = b"\x00\x00\xfe\xff\x01\x02\x03"
        with pytest.raises(ValueError, match="Could not decode"):
            read_text_auto_detect(raw)

    def test_invalid_utf8_with_bom_raises_value_error(self) -> None:
        # UTF-8 BOM followed by invalid UTF-8 bytes
        raw = b"\xef\xbb\xbf\xff\xff"
        with pytest.raises(ValueError, match="Could not decode"):
            read_text_auto_detect(raw)


# ==============================================================================
# 4. XLIFF 1.2 and 2.0 Parsing
# ==============================================================================


class TestParseXliffFormats:
    """Tests for parsing XLIFF 1.2 and XLIFF 2.0 structures."""

    def test_xliff_12_format(self) -> None:
        data = _make_xliff_12(
            [
                ("Hello", "Bonjour"),
                ("Goodbye", "Au revoir"),
            ]
        )
        summary = parse_xliff(data)
        assert summary.format == "xliff"
        assert len(summary.entries) == 2
        pairs = {str(e["source"]): str(e["target"]) for e in summary.entries}
        assert pairs["Hello"] == "Bonjour"
        assert pairs["Goodbye"] == "Au revoir"

    def test_xliff_20_format(self) -> None:
        data = _make_xliff_20(
            [
                ("Hello", "Bonjour"),
                ("Save file", "Enregistrer le fichier"),
            ]
        )
        summary = parse_xliff(data)
        assert summary.format == "xliff"
        assert len(summary.entries) == 2
        pairs = {str(e["source"]): str(e["target"]) for e in summary.entries}
        assert pairs["Hello"] == "Bonjour"
        assert pairs["Save file"] == "Enregistrer le fichier"

    def test_xliff_20_multiple_segments_in_unit(self) -> None:
        xml = (
            b'<?xml version="1.0" encoding="utf-8"?>\n'
            b'<xliff version="2.0" xmlns="urn:oasis:names:tc:xliff:document:2.0" srcLang="en" trgLang="de">\n'
            b'    <file id="f1">\n'
            b'        <unit id="u1">\n'
            b'            <segment id="s1">\n'
            b"                <source>Cat</source>\n"
            b"                <target>Katze</target>\n"
            b"            </segment>\n"
            b'            <segment id="s2">\n'
            b"                <source>Dog</source>\n"
            b"                <target>Hund</target>\n"
            b"            </segment>\n"
            b"        </unit>\n"
            b"    </file>\n"
            b"</xliff>"
        )
        summary = parse_xliff(xml)
        assert len(summary.entries) == 2
        pairs = {str(e["source"]): str(e["target"]) for e in summary.entries}
        assert pairs["Cat"] == "Katze"
        assert pairs["Dog"] == "Hund"

    def test_xliff_nested_inline_tags_stripped_correctly(self) -> None:
        xml = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<xliff version="1.2" xmlns="urn:oasis:names:tc:xliff:document:1.2">\n'
            '    <file original="test.txt" source-language="en" target-language="es" datatype="plaintext">\n'
            "        <body>\n"
            '            <trans-unit id="1">\n'
            '                <source>Click <b id="b1">here</b> to start</source>\n'
            '                <target>Haga clic <b id="b1">aquí</b> para empezar</target>\n'
            "            </trans-unit>\n"
            "        </body>\n"
            "    </file>\n"
            "</xliff>"
        ).encode()
        summary = parse_xliff(xml)
        assert len(summary.entries) == 1
        entry = summary.entries[0]
        assert entry["source"] == "Click here to start"
        assert entry["target"] == "Haga clic aquí para empezar"


# ==============================================================================
# 5. Missing Targets and Whitespace Handling
# ==============================================================================


class TestMissingTargetAndWhitespace:
    """Tests for omission of units with missing target or whitespace values."""

    def test_missing_target_skipped_in_xliff_12(self) -> None:
        data = _make_xliff_12(
            [
                ("Hello", "Bonjour"),
                ("Untranslated", None),  # Missing <target> element
                ("Goodbye", "Au revoir"),
            ]
        )
        summary = parse_xliff(data)
        assert len(summary.entries) == 2
        sources = {str(e["source"]) for e in summary.entries}
        assert "Hello" in sources
        assert "Goodbye" in sources
        assert "Untranslated" not in sources

    def test_missing_target_skipped_in_xliff_20(self) -> None:
        data = _make_xliff_20(
            [
                ("Untranslated 1", None),
                ("Translated", "Traduit"),
                ("Untranslated 2", None),
            ]
        )
        summary = parse_xliff(data)
        assert len(summary.entries) == 1
        assert summary.entries[0]["source"] == "Translated"
        assert summary.entries[0]["target"] == "Traduit"

    def test_all_missing_targets_raises_value_error(self) -> None:
        data = _make_xliff_12(
            [
                ("Only Source 1", None),
                ("Only Source 2", None),
            ]
        )
        with pytest.raises(ValueError, match="contains no source/target pairs"):
            parse_xliff(data)

    def test_empty_or_whitespace_source_and_target_omitted(self) -> None:
        data = _make_xliff_12(
            [
                ("Valid Source", "Valid Target"),
                ("   ", "Target with whitespace source"),
                ("Source with empty target", ""),
                ("Source with whitespace target", "   \t\n  "),
                ("", ""),
                ("   ", "   "),
            ]
        )
        summary = parse_xliff(data)
        assert len(summary.entries) == 1
        assert summary.entries[0]["source"] == "Valid Source"
        assert summary.entries[0]["target"] == "Valid Target"

    def test_all_whitespace_units_raises_value_error(self) -> None:
        data = _make_xliff_12(
            [
                ("   ", "   "),
                ("", "Target only"),
                ("Source only", ""),
            ]
        )
        with pytest.raises(ValueError, match="contains no source/target pairs"):
            parse_xliff(data)


# ==============================================================================
# 6. Corrupted XML and Security (DTD / XXE)
# ==============================================================================


class TestCorruptedXMLAndSecurity:
    """Tests for deterministic handling of corrupt XML syntax and forbidden DTDs."""

    def test_corrupted_unclosed_xml_raises_value_error(self) -> None:
        raw = b"<xliff><file><body><trans-unit><source>Unclosed"
        with pytest.raises(ValueError, match="Invalid glossary XML"):
            parse_xliff(raw)

    def test_corrupted_empty_data_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Invalid glossary XML"):
            parse_xliff(b"")

    def test_corrupted_non_xml_bytes_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Invalid glossary XML"):
            parse_xliff(b"\x00\x01\x02\x03\x04 not xml")

    def test_forbidden_external_entity_reference_raises_value_error(self) -> None:
        raw = (
            b'<?xml version="1.0"?>\n'
            b'<!DOCTYPE xliff [<!ENTITY xxe SYSTEM "file:///c:/windows/win.ini">]>\n'
            b"<xliff><trans-unit><source>&xxe;</source><target>Exploit</target></trans-unit></xliff>"
        )
        with pytest.raises(
            ValueError,
            match="DTD and external entities are not allowed in glossary XML",
        ):
            parse_xliff(raw)

    def test_forbidden_entity_expansion_bomb_raises_value_error(self) -> None:
        raw = (
            b'<?xml version="1.0"?>\n'
            b"<!DOCTYPE lolz [\n"
            b'<!ENTITY lol "lol">\n'
            b'<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">\n'
            b"]>\n"
            b"<xliff><trans-unit><source>&lol2;</source><target>Exploit</target></trans-unit></xliff>"
        )
        with pytest.raises(
            ValueError,
            match="DTD and external entities are not allowed in glossary XML",
        ):
            parse_xliff(raw)

    def test_forbidden_bare_doctype_raises_value_error(self) -> None:
        raw = (
            b'<?xml version="1.0"?>\n'
            b"<!DOCTYPE xliff>\n"
            b"<xliff><trans-unit><source>A</source><target>B</target></trans-unit></xliff>"
        )
        with pytest.raises(
            ValueError,
            match="DTD and external entities are not allowed in glossary XML",
        ):
            parse_xliff(raw)


# ==============================================================================
# 7. XLIFF with Various Encodings
# ==============================================================================


class TestXliffEncodings:
    """Tests for parsing XLIFF files with various character encodings."""

    def test_xliff_encoded_in_utf16_le_with_bom(self) -> None:
        xml_str = (
            '<?xml version="1.0" encoding="utf-16"?>\n'
            '<xliff version="1.2" xmlns="urn:oasis:names:tc:xliff:document:1.2">\n'
            '    <file original="test.txt" source-language="en" target-language="de" datatype="plaintext">\n'
            "        <body>\n"
            '            <trans-unit id="1">\n'
            "                <source>Window</source>\n"
            "                <target>Fenster</target>\n"
            "            </trans-unit>\n"
            "        </body>\n"
            "    </file>\n"
            "</xliff>"
        )
        raw = b"\xff\xfe" + xml_str.encode("utf-16-le")
        summary = parse_xliff(raw)
        assert summary.encoding == "utf-16-le"
        assert summary.entries[0]["source"] == "Window"
        assert summary.entries[0]["target"] == "Fenster"

    def test_xliff_encoded_in_utf8_with_bom(self) -> None:
        data = b"\xef\xbb\xbf" + _make_xliff_12([("Door", "Porte")])
        summary = parse_xliff(data)
        assert summary.encoding == "utf-8-sig"
        assert summary.entries[0]["source"] == "Door"
        assert summary.entries[0]["target"] == "Porte"

    def test_xliff_encoded_in_windows_1252(self) -> None:
        xml_str = (
            '<?xml version="1.0" encoding="windows-1252"?>\n'
            '<xliff version="1.2" xmlns="urn:oasis:names:tc:xliff:document:1.2">\n'
            '    <file original="test.txt" source-language="en" target-language="fr" datatype="plaintext">\n'
            "        <body>\n"
            '            <trans-unit id="1">\n'
            "                <source>Price: 100 €</source>\n"
            "                <target>Prix: 100 €</target>\n"
            "            </trans-unit>\n"
            "        </body>\n"
            "    </file>\n"
            "</xliff>"
        )
        raw = xml_str.encode("windows-1252")
        summary = parse_xliff(raw)
        assert "100 €" in str(summary.entries[0]["source"])
        assert "100 €" in str(summary.entries[0]["target"])
