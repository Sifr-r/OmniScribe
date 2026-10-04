"""Tests for :mod:`omniscribe.core.translate.nllb`."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from omniscribe.core.translate.nllb import (
    DEFAULT_SOURCE_LANGUAGE,
    LANGUAGE_CODE_MAP,
    SUPPORTED_LANGUAGES,
    NLLBEngine,
    UnsupportedLanguageError,
    resolve_nllb_code,
)

_CLIENT_SCREEN = (
    Path(__file__).resolve().parents[3]
    / "client"
    / "lib"
    / "features"
    / "translation"
    / "translation_screen.dart"
)


def test_nllb_resolve_nllb_code_known():
    assert resolve_nllb_code("French") == "fra_Latn"
    assert resolve_nllb_code("english") == "eng_Latn"
    assert resolve_nllb_code("Chinese") == "zho_Hans"
    # Already a code
    assert resolve_nllb_code("deu_Latn") == "deu_Latn"
    # Lookup is case- and whitespace-insensitive.
    assert resolve_nllb_code("  FRENCH  ") == "fra_Latn"


def test_nllb_resolve_nllb_code_rejects_unknown():
    """Unknown labels must fail loudly, not silently become English.

    This replaced the old ``resolve_nllb_code("Klingon") == "eng_Latn"``
    assertion: silently translating an unsupported request into English is a
    correctness bug (assessment finding 4).
    """
    with pytest.raises(UnsupportedLanguageError) as excinfo:
        resolve_nllb_code("Klingon")
    assert "Klingon" in str(excinfo.value)
    # Also a ValueError, so Pydantic validators surface it as a 422.
    assert isinstance(excinfo.value, ValueError)
    with pytest.raises(UnsupportedLanguageError):
        resolve_nllb_code("")


def test_nllb_regression_client_labels_resolve():
    """Every language the Flutter client offers must resolve to its own code.

    Regression guard for the two labels that used to resolve to
    ``eng_Latn``: "Chinese (Simplified)" and "Korean".
    """
    assert resolve_nllb_code("Chinese (Simplified)") == "zho_Hans"
    assert resolve_nllb_code("Korean") == "kor_Hang"
    for label in SUPPORTED_LANGUAGES:
        code = resolve_nllb_code(label)
        assert code != DEFAULT_SOURCE_LANGUAGE or label == "English", label
        assert "_" in code


def test_nllb_client_language_list_is_covered():
    """Parse the Dart label list and assert every entry is supported.

    Guards the server/client contract: adding a label to the client without a
    mapping entry would otherwise be a 422 at runtime for the user.
    """
    source = _CLIENT_SCREEN.read_text(encoding="utf-8")
    match = re.search(
        r"static const List<String> _languages\s*=\s*\[(.*?)\]",
        source,
        re.DOTALL,
    )
    assert match is not None, "client _languages list not found"
    labels = re.findall(r"'([^']+)'", match.group(1))
    assert labels, "client _languages list parsed empty"
    for label in labels:
        assert label in SUPPORTED_LANGUAGES, f"{label!r} missing from server contract"
        resolve_nllb_code(label)


def test_nllb_language_code_map_has_basics():
    for name in ("english", "spanish", "french", "german", "chinese", "japanese"):
        assert name in LANGUAGE_CODE_MAP
        assert "_" in LANGUAGE_CODE_MAP[name]


def test_nllb_source_language_is_pinned_to_english():
    """Source selection is an explicit scope decision, not an accident."""
    assert DEFAULT_SOURCE_LANGUAGE == "eng_Latn"


@pytest.mark.parametrize("language", ["not_A_Code", "fra_LatX", "xxx_Latn"])
async def test_invalid_nllb_code_is_rejected_before_loading(
    monkeypatch: pytest.MonkeyPatch,
    language: str,
) -> None:
    def forbidden_load() -> None:
        pytest.fail("invalid target attempted to load model weights")

    engine = NLLBEngine()
    monkeypatch.setattr(engine, "_ensure_loaded", forbidden_load)
    with pytest.raises(UnsupportedLanguageError):
        await engine.translate("source text", language)
