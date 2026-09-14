"""Direct unit and property tests for :mod:`omniscribe.core.translate.workflow`."""

from __future__ import annotations

import builtins
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from omniscribe.core.translate import nodes as translation_nodes
from omniscribe.core.translate.config import (
    AsyncTranslationUnavailable,
    TranslationError,
    TranslationSettings,
)
from omniscribe.core.translate.workflow import (
    TranslationState,
    _Chunker,
    chunk_text,
    evaluate_node,
    get_translation_app,
    retrieve_lexicon_context,
    run_translation,
    should_refine,
    translate_node,
    translation_app,
)

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

# ---------------------------------------------------------------------------
# get_translation_app / lazy compilation tests
# ---------------------------------------------------------------------------


def test_get_translation_app_raises_when_langgraph_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_translation_app raises AsyncTranslationUnavailable when langgraph import fails."""
    get_translation_app.cache_clear()

    orig_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "langgraph" or name.startswith("langgraph."):
            raise ImportError(f"No module named '{name}'")
        return orig_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    try:
        with pytest.raises(AsyncTranslationUnavailable, match="langgraph"):
            get_translation_app()
    finally:
        get_translation_app.cache_clear()


def test_get_translation_app_compiles_when_langgraph_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_translation_app builds and compiles the StateGraph when langgraph is available."""
    get_translation_app.cache_clear()

    mock_graph_module = MagicMock()
    mock_workflow = MagicMock()
    mock_compiled_app = MagicMock()
    mock_workflow.compile.return_value = mock_compiled_app
    mock_graph_module.StateGraph.return_value = mock_workflow
    mock_graph_module.START = "START"
    mock_graph_module.END = "END"

    monkeypatch.setitem(sys.modules, "langgraph", MagicMock())
    monkeypatch.setitem(sys.modules, "langgraph.graph", mock_graph_module)

    try:
        app = get_translation_app()
        assert app is mock_compiled_app

        # Verify nodes and edges configuration
        mock_workflow.add_node.assert_any_call("retrieve", retrieve_lexicon_context)
        mock_workflow.add_node.assert_any_call("translate", translate_node)
        mock_workflow.add_node.assert_any_call("evaluate", evaluate_node)

        mock_workflow.add_edge.assert_any_call("START", "retrieve")
        mock_workflow.add_edge.assert_any_call("retrieve", "translate")
        mock_workflow.add_edge.assert_any_call("translate", "evaluate")
        mock_workflow.add_conditional_edges.assert_any_call(
            "evaluate", should_refine, {"translate": "translate", "end": "END"}
        )
    finally:
        get_translation_app.cache_clear()


def test_lazy_translation_app_delegates_to_get_translation_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """translation_app.invoke delegates to get_translation_app().ainvoke."""
    mock_app = MagicMock()
    mock_app.ainvoke = AsyncMock(return_value={"translated_chunk": "Translated text"})
    monkeypatch.setattr(
        "omniscribe.core.translate.workflow.get_translation_app",
        lambda: mock_app,
    )

    state: TranslationState = {
        "source_chunk": "Input text",
        "target_language": "German",
    }
    result = translation_app.invoke(state)
    assert result == {"translated_chunk": "Translated text"}
    mock_app.ainvoke.assert_awaited_once_with(state)


# ---------------------------------------------------------------------------
# _Chunker and chunk_text tests
# ---------------------------------------------------------------------------


def test_chunker_empty_and_basic_accumulation() -> None:
    """_Chunker handles empty inputs and accumulates text within max_chunk_size."""
    chunker = _Chunker(max_chunk_size=30)
    assert chunker.finalize() == []

    chunker.add("", " ")
    assert chunker.finalize() == []

    chunker.add("first", " ")
    chunker.add("second", " ")
    assert chunker.finalize() == ["first second"]


def test_chunker_flushes_when_exceeding_capacity() -> None:
    """_Chunker flushes current accumulator when adding candidate exceeds max_chunk_size."""
    chunker = _Chunker(max_chunk_size=15)
    chunker.add("hello", " ")
    chunker.add("beautiful", " ")  # "hello beautiful" is 15 -> fits
    chunker.add(
        "world", " "
    )  # "hello beautiful world" is 21 > 15 -> flushes "hello beautiful"
    assert chunker.finalize() == ["hello beautiful", "world"]


def test_chunk_text_validation() -> None:
    """chunk_text enforces string inputs and positive max_chunk_size."""
    with pytest.raises(TypeError, match="text must be a string"):
        chunk_text(123)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="max_chunk_size must be greater than zero"):
        chunk_text("hello", max_chunk_size=0)

    with pytest.raises(ValueError, match="max_chunk_size must be greater than zero"):
        chunk_text("hello", max_chunk_size=-10)


def test_chunk_text_empty_and_short() -> None:
    """Empty string returns empty list; text shorter than max_chunk_size returns single chunk."""
    assert chunk_text("") == []
    assert chunk_text("Hello world", max_chunk_size=100) == ["Hello world"]


def test_chunk_text_hierarchical_paragraph_splitting() -> None:
    """chunk_text prefers paragraph boundaries before lines and words."""
    p1 = "First paragraph of text."
    p2 = "Second paragraph of text."
    p3 = "Third paragraph of text."
    text = f"{p1}\n\n{p2}\n\n{p3}"

    chunks = chunk_text(text, max_chunk_size=35)
    assert chunks == [p1, p2, p3]


def test_chunk_text_hierarchical_line_splitting() -> None:
    """chunk_text splits long paragraphs by line when paragraph exceeds max_chunk_size."""
    l1 = "Line one with some content"
    l2 = "Line two with more content"
    paragraph = f"{l1}\n{l2}"

    chunks = chunk_text(paragraph, max_chunk_size=30)
    assert chunks == [l1, l2]


def test_chunk_text_hierarchical_word_splitting() -> None:
    """chunk_text splits long lines by word when line exceeds max_chunk_size."""
    line = "alpha beta gamma delta epsilon"
    chunks = chunk_text(line, max_chunk_size=12)
    assert chunks == ["alpha beta", "gamma delta", "epsilon"]


@settings(max_examples=100, deadline=None)
@given(
    words=st.lists(
        st.text(
            alphabet=st.characters(whitelist_categories=["Lu", "Ll", "Nd"]),
            min_size=1,
            max_size=10,
        ),
        min_size=1,
        max_size=40,
    ),
    max_chunk_size=st.integers(min_value=15, max_value=80),
)
def test_chunk_text_content_preservation(words: list[str], max_chunk_size: int) -> None:
    """chunk_text preserves all original words and never returns empty chunks."""
    text = " ".join(words)
    chunks = chunk_text(text, max_chunk_size=max_chunk_size)

    assert all(len(c.strip()) > 0 for c in chunks)
    reconstructed_words = [w for c in chunks for w in c.split()]
    assert reconstructed_words == words


# ---------------------------------------------------------------------------
# State transitions and nodes with mocked LLM
# ---------------------------------------------------------------------------


def test_retrieve_lexicon_context_node(monkeypatch: pytest.MonkeyPatch) -> None:
    """retrieve_lexicon_context returns formatted RAG context from hits."""
    state: TranslationState = {
        "source_chunk": "quantum decoherence",
        "target_language": "French",
    }
    result = retrieve_lexicon_context(state)
    assert "rag_context" in result
    assert isinstance(result["rag_context"], list)


async def test_translate_node_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """translate_node calls call_llm and increments attempts in state."""

    async def mock_call_llm(**kwargs: Any) -> str:
        return "Bonjour le monde"

    monkeypatch.setattr(translation_nodes, "call_llm", mock_call_llm)

    state: TranslationState = {
        "source_chunk": "Hello world",
        "target_language": "French",
        "attempts": 0,
        "settings": TranslationSettings(
            api_base="https://example.test/v1",
            api_key="test-key",
            model="test-model",
        ),
    }

    result = await translate_node(state)
    assert result["translated_chunk"] == "Bonjour le monde"
    assert result["attempts"] == 1


async def test_translate_node_handles_llm_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """translate_node converts LLM exception into [Translation Error: ...] marker."""

    async def failing_call_llm(**kwargs: Any) -> str:
        raise RuntimeError("Connection dropped")

    monkeypatch.setattr(translation_nodes, "call_llm", failing_call_llm)

    state: TranslationState = {
        "source_chunk": "Hello world",
        "target_language": "French",
        "attempts": 1,
        "settings": TranslationSettings(
            api_base="https://example.test/v1",
            api_key="test-key",
            model="test-model",
        ),
    }

    result = await translate_node(state)
    assert "[Translation Error: Connection dropped]" in str(result["translated_chunk"])
    assert result["attempts"] == 2


async def test_evaluate_node_fast_path_and_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """evaluate_node handles error short-circuit and LLM evaluation scores."""
    # Fast path: error string
    state_err: TranslationState = {
        "source_chunk": "Hello",
        "translated_chunk": "[Translation Error: 503]",
        "attempts": 1,
    }
    res_err = await evaluate_node(state_err)
    assert res_err["evaluation_score"] == 0.0

    # Normal LLM evaluation path (requires non-empty rag_context to avoid §2.5 fast-accept)
    mock_llm_eval = AsyncMock(return_value=(0.95, "Accurate translation"))
    monkeypatch.setattr(translation_nodes, "_llm_evaluate_translation", mock_llm_eval)

    state_ok: TranslationState = {
        "source_chunk": "Hello world, this is a test.",
        "translated_chunk": "Bonjour le monde, ceci est un test.",
        "target_language": "French",
        "rag_context": ["term: translation"],
        "attempts": 1,
        "settings": TranslationSettings(
            api_base="https://example.test/v1",
            api_key="test-key",
            model="test-model",
        ),
    }
    res_ok = await evaluate_node(state_ok)
    assert res_ok["evaluation_score"] == 0.95
    assert res_ok["feedback"] == "Accurate translation"


def test_should_refine_state_machine_transitions() -> None:
    """should_refine determines transition to 'translate' or 'end'."""
    settings_obj = TranslationSettings(acceptance_score=0.8, max_attempts=3)

    # Score >= acceptance_score -> end
    state_done: TranslationState = {
        "evaluation_score": 0.9,
        "settings": settings_obj,
    }
    assert should_refine(state_done) == "end"

    # Score < acceptance_score -> translate
    state_refine: TranslationState = {
        "evaluation_score": 0.5,
        "settings": settings_obj,
    }
    assert should_refine(state_refine) == "translate"

    # Default missing score (1.0) -> end
    state_default: TranslationState = {
        "settings": settings_obj,
    }
    assert should_refine(state_default) == "end"


def test_run_translation_input_validation() -> None:
    """run_translation validates input types and rejects empty languages."""
    with pytest.raises(TypeError, match="text must be a string"):
        run_translation(123)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="target_language must be a non-empty string"):
        run_translation("Hello", target_language="")

    with pytest.raises(ValueError, match="target_language must be a non-empty string"):
        run_translation("Hello", target_language="   ")

    assert run_translation("   ") == ""


def test_run_translation_end_to_end_with_mocked_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_translation invokes the translation app for each text chunk and combines results."""
    mock_app = MagicMock()

    async def fake_ainvoke(state: TranslationState, *args: Any, **kwargs: Any) -> Any:
        return {"translated_chunk": f"Translated({state['source_chunk']})"}

    mock_app.ainvoke = AsyncMock(side_effect=fake_ainvoke)

    monkeypatch.setattr(
        "omniscribe.core.translate.workflow.get_translation_app",
        lambda: mock_app,
    )
    monkeypatch.setattr(
        "omniscribe.core.translate.workflow.chunk_text",
        lambda text: ["Chunk 1", "Chunk 2"],
    )

    result = run_translation("Dummy input", target_language="Spanish")
    assert result == "Translated(Chunk 1)\n\nTranslated(Chunk 2)"
    assert mock_app.ainvoke.await_count == 2


async def test_run_translation_bridges_running_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_translation works when called from inside a running event loop."""
    mock_app = MagicMock()
    mock_app.ainvoke = AsyncMock(return_value={"translated_chunk": "Bonjour"})
    monkeypatch.setattr(
        "omniscribe.core.translate.workflow.get_translation_app",
        lambda: mock_app,
    )

    result = run_translation("Hello world", target_language="French")
    assert result == "Bonjour"


# ---------------------------------------------------------------------------
# Translate/evaluate loop: retry convergence, fail-safe bookkeeping, failures
# ---------------------------------------------------------------------------


def _loop_settings(**overrides: Any) -> TranslationSettings:
    kwargs: dict[str, Any] = {
        "api_base": "https://example.test/v1",
        "api_key": "test-key",
        "model": "test-model",
    }
    kwargs.update(overrides)
    return TranslationSettings(**kwargs)


def _initial_state(settings: TranslationSettings) -> TranslationState:
    return {
        "source_chunk": "Hello world, this is a test.",
        "target_language": "French",
        "rag_context": [],
        "translated_chunk": "",
        "evaluation_score": 1.0,
        "feedback": "",
        "attempts": 0,
        "settings": settings,
    }


async def _drive_loop(state: TranslationState) -> TranslationState:
    """Drive the real nodes through the same cycle the compiled graph wires."""
    settings = state["settings"]
    for _ in range(settings.max_attempts):
        state.update(await translate_node(state))  # type: ignore[arg-type]
        state.update(await evaluate_node(state))  # type: ignore[arg-type]
        if should_refine(state) == "end":
            break
    return state


async def test_translation_loop_converges_after_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A below-acceptance score retries with feedback; a passing score exits."""
    scores = iter([0.4, 0.95])
    translations = iter(["Une premiere sortie ici.", "Une meilleure sortie ici."])
    prompts: list[str] = []

    async def fake_translate_llm(**kwargs: Any) -> str:
        prompts.append(kwargs["messages"][0]["content"])
        return next(translations)

    async def fake_judge(state: Any) -> tuple[float, str]:
        return next(scores), "needs work"

    monkeypatch.setattr(translation_nodes, "call_llm", fake_translate_llm)
    monkeypatch.setattr(translation_nodes, "_llm_evaluate_translation", fake_judge)

    state = await _drive_loop(
        _initial_state(_loop_settings(acceptance_score=0.8, max_attempts=3))
    )

    assert state["attempts"] == 2
    assert state["translated_chunk"] == "Une meilleure sortie ici."
    assert state["best_score"] == 0.95
    assert len(prompts) == 2
    assert "needs work" in prompts[1]


async def test_translation_loop_max_attempts_keeps_best(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """At max attempts the best-scoring attempt wins, not the last one."""
    scores = iter([0.5, 0.3])
    translations = iter(["Bonne tentative numero un.", "Sortie finale bien pire."])

    async def fake_translate_llm(**kwargs: Any) -> str:
        return next(translations)

    async def fake_judge(state: Any) -> tuple[float, str]:
        return next(scores), "scored"

    monkeypatch.setattr(translation_nodes, "call_llm", fake_translate_llm)
    monkeypatch.setattr(translation_nodes, "_llm_evaluate_translation", fake_judge)

    state = await _drive_loop(
        _initial_state(_loop_settings(acceptance_score=0.9, max_attempts=2))
    )

    assert state["attempts"] == 2
    assert state["translated_chunk"] == "Bonne tentative numero un."
    assert state["best_score"] == 0.5
    assert not state.get("failed")


async def test_translation_loop_reverts_to_best_after_late_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An LLM error after a good attempt reverts to the best translation."""
    calls = {"n": 0}

    async def flaky_llm(**kwargs: Any) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            return "Premiere bonne sortie de test."
        raise RuntimeError("socket hangup")

    async def fake_judge(state: Any) -> tuple[float, str]:
        return 0.6, "scored"

    monkeypatch.setattr(translation_nodes, "call_llm", flaky_llm)
    monkeypatch.setattr(translation_nodes, "_llm_evaluate_translation", fake_judge)

    state = await _drive_loop(
        _initial_state(_loop_settings(acceptance_score=0.9, max_attempts=3))
    )

    assert state["attempts"] == 2
    assert state["translated_chunk"] == "Premiere bonne sortie de test."
    assert state["feedback"] == "Reverted to best attempt."
    assert not state.get("failed")


async def test_translation_loop_marks_failed_when_errors_exhaust_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every attempt erroring at max attempts marks the state failed."""
    judge_calls: list[Any] = []

    async def failing_llm(**kwargs: Any) -> str:
        raise RuntimeError("connection refused")

    async def fake_judge(state: Any) -> tuple[float, str]:
        judge_calls.append(state)
        return 1.0, ""

    monkeypatch.setattr(translation_nodes, "call_llm", failing_llm)
    monkeypatch.setattr(translation_nodes, "_llm_evaluate_translation", fake_judge)

    state = await _drive_loop(
        _initial_state(_loop_settings(acceptance_score=0.8, max_attempts=1))
    )

    assert state["attempts"] == 1
    assert state["failed"] is True
    assert "[Translation Error: connection refused]" in state["translated_chunk"]
    assert judge_calls == []


def test_run_translation_raises_translation_error_on_failed_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_translation raises TranslationError when the graph marks the chunk failed."""
    mock_app = MagicMock()
    mock_app.ainvoke = AsyncMock(
        return_value={"failed": True, "translated_chunk": "[Translation Error: boom]"}
    )
    monkeypatch.setattr(
        "omniscribe.core.translate.workflow.get_translation_app",
        lambda: mock_app,
    )

    with pytest.raises(TranslationError, match="Translation failed"):
        run_translation("Hello world", target_language="French")


# ---------------------------------------------------------------------------
# Real compiled LangGraph app (requires the async-translation extra)
# ---------------------------------------------------------------------------


def test_compiled_graph_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """The compiled graph wires retrieve → translate → evaluate → end."""
    pytest.importorskip("langgraph")
    get_translation_app.cache_clear()
    try:

        async def fake_translate_llm(**kwargs: Any) -> str:
            return "Bonjour le monde, ceci est un essai."

        async def fake_judge(state: Any) -> tuple[float, str]:
            return 0.95, "faithful"

        monkeypatch.setattr(translation_nodes, "call_llm", fake_translate_llm)
        monkeypatch.setattr(translation_nodes, "_llm_evaluate_translation", fake_judge)

        state = _initial_state(_loop_settings())
        result = translation_app.invoke(state)

        assert result["translated_chunk"] == "Bonjour le monde, ceci est un essai."
        assert result["attempts"] == 1
        assert result["best_score"] == 0.95
    finally:
        get_translation_app.cache_clear()


def test_compiled_graph_retry_loop_exits_on_acceptance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The conditional edge retranslates on a low score and exits on acceptance."""
    pytest.importorskip("langgraph")
    get_translation_app.cache_clear()
    try:
        scores = iter([0.4, 0.95])
        translations = iter(["Une premiere sortie ici.", "Une meilleure sortie ici."])

        async def fake_translate_llm(**kwargs: Any) -> str:
            return next(translations)

        async def fake_judge(state: Any) -> tuple[float, str]:
            return next(scores), "needs work"

        monkeypatch.setattr(translation_nodes, "call_llm", fake_translate_llm)
        monkeypatch.setattr(translation_nodes, "_llm_evaluate_translation", fake_judge)

        state = _initial_state(_loop_settings(acceptance_score=0.8, max_attempts=3))
        result = translation_app.invoke(state)

        assert result["attempts"] == 2
        assert result["translated_chunk"] == "Une meilleure sortie ici."
        assert result["best_score"] == 0.95
    finally:
        get_translation_app.cache_clear()
