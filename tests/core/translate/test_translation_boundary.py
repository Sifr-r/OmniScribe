"""Focused tests for the optional async translation dependency boundary."""

from __future__ import annotations

from omniscribe.core.translate.config import TranslationSettings

# ---------------------------------------------------------------------------
# ``test_translation_base_imports_do_not_require_async_extras`` was removed
# in Sprint 6 P3 cleanup (audit catalog P3 dead-code item). It used
# ``omniscribe.api.tasks.process_translation_task`` — the Celery async
# translation task that was removed in the API rebuild. The Celery
# worker entry was on the deferred path per audit #11; the
# ``async-translation`` extra still exists but the Celery task module
# does not. The test's subprocess was guarded by
# ``pytest.importorskip("omniscribe.api")`` so it silently skipped
# at collection. The catalog called this out as silently inflating
# the test count without running.
# ---------------------------------------------------------------------------


def test_optional_extras_split_lexicon_rag_dependencies():
    """The ``async-translation`` extra MUST stay light.

    Regression guard: future "I'll just add this here" PRs are easy
    to write; this test makes sure lancedb + sentence-transformers
    stay parked in the separate ``lexicon`` (formerly ``memory``) extra
    and don't bloat the async-translation install. See
    ``docs/lexicon-migration-spec.md`` §10 for the rename.
    """
    import tomllib
    from pathlib import Path

    pyproject = Path(__file__).resolve().parents[3] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_bytes().decode("utf-8"))
    extras = data["project"]["optional-dependencies"]

    assert "async-translation" in extras, "async-translation extra is required"
    assert "lexicon" in extras, (
        "lexicon extra is required (it owns lancedb + sentence-transformers)"
    )

    async_deps = " ".join(extras["async-translation"])
    for heavy in ("lancedb", "sentence-transformers", "chromadb"):
        assert heavy not in async_deps, (
            f"{heavy!r} must not appear in the async-translation extra; "
            f"it belongs in the 'lexicon' extra"
        )

    lexicon_deps = " ".join(extras["lexicon"])
    for required in ("lancedb", "sentence-transformers", "pyarrow"):
        assert required in lexicon_deps, (
            f"{required!r} is missing from the 'lexicon' extra"
        )


async def test_translate_node_uses_injected_settings(monkeypatch):
    """translate_node routes through call_llm (refactor §2.2 — unify LLM dispatch).

    Pre-fix, translate_node instantiated AsyncOpenAI directly and bypassed the
    shared ``call_llm`` wrapper, so it had no retry/backoff and was a divergent
    fifth call path. After the fix it must go through ``call_llm`` like
    ``evaluate_node`` / ``api.services.ai._complete_text`` already do.
    """
    import omniscribe.core.translate.nodes as translation

    captured: dict[str, object] = {}

    async def fake_call_llm(**kwargs):
        captured.update(kwargs)
        return "Bonjour"

    monkeypatch.setattr(translation, "call_llm", fake_call_llm)

    state = {
        "source_chunk": "Hello",
        "target_language": "French",
        "rag_context": [],
        "translated_chunk": "",
        "evaluation_score": 1.0,
        "feedback": "",
        "attempts": 0,
        "settings": TranslationSettings(
            api_base="https://example.test/v1",
            api_key="test-key",
            model="openai/test-model",
        ),
    }

    result = await translation.translate_node(state)

    assert result["translated_chunk"] == "Bonjour"
    assert captured["model"] == "openai/test-model"
    assert captured["api_base"] == "https://example.test/v1"
    assert captured["api_key"] == "test-key"
    assert captured["temperature"] == 0.3
    msgs = captured["messages"]
    assert isinstance(msgs, list) and msgs and isinstance(msgs[0], dict)
    assert "SOURCE:" in msgs[0]["content"]


async def test_translate_node_preserves_error_prefix_on_call_llm_failure(monkeypatch):
    """Refactor §2.2 — the ``[Translation Error: ...]`` prefix contract is preserved.

    ``evaluate_node`` short-circuits on the ``[Translation Error`` substring
    (line 237 of ``core/translate/workflow.py``), so a switch from ``AsyncOpenAI`` to
    ``call_llm`` must keep producing that prefix when the LLM raises.
    """
    import omniscribe.core.translate.nodes as translation

    async def boom(**_kwargs):
        raise RuntimeError("upstream gone")

    monkeypatch.setattr(translation, "call_llm", boom)

    state = {
        "source_chunk": "Hello",
        "target_language": "French",
        "rag_context": [],
        "translated_chunk": "",
        "evaluation_score": 1.0,
        "feedback": "",
        "attempts": 0,
        "settings": TranslationSettings(
            api_base="https://example.test/v1",
            api_key="test-key",
            model="openai/test-model",
        ),
    }

    result = await translation.translate_node(state)
    translated = result["translated_chunk"]
    assert translated.startswith("[Translation Error")  # type: ignore[union-attr]
    assert "upstream gone" in translated  # type: ignore[operator]


async def test_translate_node_includes_glossary_and_memory(monkeypatch):
    """When the new optional state fields are populated, they must end up in the prompt."""
    from omniscribe.core.translate import nodes as translation_mod

    captured: dict[str, object] = {}

    async def fake_call_llm(**kwargs):
        captured["messages"] = kwargs.get("messages")
        return "translated"

    monkeypatch.setattr(translation_mod, "call_llm", fake_call_llm)

    state = {
        "source_chunk": "Bonjour le monde",
        "target_language": "English",
        "glossary_prompt_block": "GLOSSARY: Bonjour = Hello",
        "entity_memory_prompt_block": "NAMES: Paris",
        "sliding_window": "previously translated text",
    }
    out = await translation_mod.translate_node(state)
    assert out["translated_chunk"] == "translated", out
    messages = captured.get("messages")
    assert isinstance(messages, list) and messages and isinstance(messages[0], dict)
    prompt = messages[0]["content"]
    assert "GLOSSARY: Bonjour = Hello" in prompt
    assert "NAMES: Paris" in prompt
    assert "PREVIOUS CONTEXT" in prompt
    assert "previously translated text" in prompt
    assert "SOURCE:" in prompt
