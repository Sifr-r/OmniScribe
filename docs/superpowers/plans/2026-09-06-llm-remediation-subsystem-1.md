# LLM Remediation — Subsystem 1 (Translation + Lexicon) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the translate/evaluate loop into production, fix RAG prompt hygiene/budget, and make the lexicon a real hybrid (RRF keyword+vector) retrieval store with upsert, idempotent migration, index creation, and model guards.

**Architecture:** Two packages change: `core/translate/` (nodes/workflow/tree/service get judge wiring, a unified prompt builder, script-aware length bands, config fields) and `core/lexicon/` (LanceDB store gains keyword leg + RRF fusion, candidate-term query embeddings, HNSW index creation, `_meta` model guard, upsert-with-embedding-reuse, idempotent migration). All changes TDD; fast-tier pytest only.

**Tech Stack:** Python 3.12, LangGraph, LanceDB + pyarrow, sentence-transformers (mocked in tests), pytest.

**Spec:** `docs/superpowers/specs/2026-09-06-llm-remediation-design.md` (items 1–17).

**Gotchas (from project memory):**
- Test-pinned seams: hybrid pass-through wrappers and re-exports (`workflow.py` re-exports `_llm_evaluate_translation`, `_state_settings`, `parse_evaluation_response`, `_FENCED_JSON_RE`, `_extract_json_object`) must keep existing names — extend, don't remove.
- Existing lexicon tests use a fake hash-based `EmbeddingModel`; keep the Protocol shape, extend fakes with `dim`/`model_name` where needed.
- If mypy fails on a corrupted orjson namespace pkg, use `uv run --with orjson mypy <paths>` (venv may have been rebuilt since).
- Run tests via `uv run pytest` (fast tier). Lexicon tests must not import pyarrow at module top unguarded — tests import the store lazily inside test functions (existing pattern in `tests/core/lexicon/`).

---

## Part A — Translation pipeline

### Task A1: TranslationSettings — new fields + env fallback logging

**Files:**
- Modify: `src/omniscribe/core/translate/config.py`
- Test: `tests/core/translate/test_translation_config.py`

- [ ] **Step 1: Write failing tests** (append to `test_translation_config.py`)

```python
def test_new_fields_defaults() -> None:
    s = TranslationSettings()
    assert s.lexicon_result_count == 3
    assert s.lexicon_min_score == 0.35
    assert s.evaluate_enabled is True
    assert s.max_tokens == 2048
    assert s.entity_memory_cap == 20


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMNISCRIBE_TRANSLATION_EVALUATE", "false")
    monkeypatch.setenv("OMNISCRIBE_TRANSLATION_LEXICON_RESULT_COUNT", "5")
    monkeypatch.setenv("OMNISCRIBE_TRANSLATION_LEXICON_MIN_SCORE", "0.5")
    monkeypatch.setenv("OMNISCRIBE_TRANSLATION_MAX_TOKENS", "4096")
    monkeypatch.setenv("OMNISCRIBE_TRANSLATION_ENTITY_MEMORY_CAP", "10")
    s = TranslationSettings.from_env()
    assert s.evaluate_enabled is False
    assert s.lexicon_result_count == 5
    assert s.lexicon_min_score == 0.5
    assert s.max_tokens == 4096
    assert s.entity_memory_cap == 10


def test_invalid_env_warns_and_falls_back(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("OMNISCRIBE_TRANSLATION_MAX_ATTEMPTS", "notanint")
    with caplog.at_level(logging.WARNING, logger="omniscribe.core.translate.config"):
        s = TranslationSettings.from_env()
    assert s.max_attempts == DEFAULT_TRANSLATION_MAX_ATTEMPTS
    assert "OMNISCRIBE_TRANSLATION_MAX_ATTEMPTS" in caplog.text
```

- [ ] **Step 2: Run** `uv run pytest tests/core/translate/test_translation_config.py -q` — expect FAIL (fields missing).

- [ ] **Step 3: Implement** in `config.py`:

Add module logger + defaults:

```python
import logging
logger = logging.getLogger(__name__)

DEFAULT_TRANSLATION_LEXICON_RESULT_COUNT = 3
DEFAULT_TRANSLATION_LEXICON_MIN_SCORE = 0.35
DEFAULT_TRANSLATION_EVALUATE = True
DEFAULT_TRANSLATION_MAX_TOKENS = 2048
DEFAULT_TRANSLATION_ENTITY_MEMORY_CAP = 20
```

Extend the frozen dataclass (keep `slots=True`):

```python
    lexicon_result_count: int = DEFAULT_TRANSLATION_LEXICON_RESULT_COUNT
    lexicon_min_score: float = DEFAULT_TRANSLATION_LEXICON_MIN_SCORE
    evaluate_enabled: bool = DEFAULT_TRANSLATION_EVALUATE
    max_tokens: int = DEFAULT_TRANSLATION_MAX_TOKENS
    entity_memory_cap: int = DEFAULT_TRANSLATION_ENTITY_MEMORY_CAP
```

Extend `__post_init__` validation: the three ints (`lexicon_result_count`, `max_tokens`, `entity_memory_cap`) must be non-bool ints >= 1; `lexicon_min_score` a non-bool number in [0.0, 1.0].

Extend `from_env`:

```python
            lexicon_result_count=_int_env(
                "OMNISCRIBE_TRANSLATION_LEXICON_RESULT_COUNT",
                DEFAULT_TRANSLATION_LEXICON_RESULT_COUNT,
                minimum=1,
            ),
            lexicon_min_score=_float_env(
                "OMNISCRIBE_TRANSLATION_LEXICON_MIN_SCORE",
                DEFAULT_TRANSLATION_LEXICON_MIN_SCORE,
                minimum=0.0,
                maximum=1.0,
            ),
            evaluate_enabled=_bool_env(
                "OMNISCRIBE_TRANSLATION_EVALUATE", DEFAULT_TRANSLATION_EVALUATE
            ),
            max_tokens=_int_env(
                "OMNISCRIBE_TRANSLATION_MAX_TOKENS", DEFAULT_TRANSLATION_MAX_TOKENS, minimum=1
            ),
            entity_memory_cap=_int_env(
                "OMNISCRIBE_TRANSLATION_ENTITY_MEMORY_CAP",
                DEFAULT_TRANSLATION_ENTITY_MEMORY_CAP,
                minimum=1,
            ),
```

Add `_bool_env` and add logging to `_int_env`/`_float_env` (every fallback path logs):

```python
def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    logger.warning(
        "Env %s=%r is not a valid boolean; using default %s", name, raw, default
    )
    return default
```

In `_int_env`: on `ValueError` and on `parsed < minimum`, log
`logger.warning("Env %s=%r invalid; using default %s", name, raw, default)` before `return default`. Same in `_float_env` (all three fallback branches).

Also extend `from_mapping` with the five keys mirroring `from_env` (`_bool_value` helper accepting bool or the same string set; invalid numeric strings already raise `ValueError` there — keep).

- [ ] **Step 4: Run** the config test file — expect PASS.
- [ ] **Step 5: Commit** `git add -A && git commit -m "translate: settings fields for evaluate toggle, lexicon knobs, token budget + env fallback warnings"`

### Task A2: Script-aware length bands

**Files:**
- Create: `src/omniscribe/core/translate/length_bands.py`
- Test: `tests/core/translate/test_length_bands.py`

- [ ] **Step 1: Write failing tests**

```python
import pytest

from omniscribe.core.translate.length_bands import effective_band


def test_same_script_uses_defaults() -> None:
    lo, hi = effective_band("Hello world, this is text.", "Hola mundo, esto es texto.")
    assert (lo, hi) == (0.1, 2.5)


def test_cjk_target_shrinks_upper_bound() -> None:
    src = "Hello world, this is a longer English source paragraph."
    tgt = "こんにちは世界、これは日本語の段落です。"
    lo, hi = effective_band(src, tgt)
    assert hi < 2.5
    assert lo <= 0.1


def test_cjk_source_expands_upper_bound() -> None:
    src = "こんにちは世界、これは日本語の段落です。"
    tgt = "Hello world, this is a longer English translation paragraph."
    lo, hi = effective_band(src, tgt)
    assert hi > 2.5


@pytest.mark.parametrize("empty", ["", "   "])
def test_empty_input_falls_back_to_defaults(empty: str) -> None:
    assert effective_band(empty, "x") == (0.1, 2.5)
    assert effective_band("x", empty) == (0.1, 2.5)
```

- [ ] **Step 2: Run** — expect FAIL (module missing).

- [ ] **Step 3: Implement** `length_bands.py`:

```python
"""Script-aware length-ratio bands for translation sanity checks.

Flat char-ratio bands (0.1-2.5) misfire across scripts: English→CJK
typically *shrinks* 2-4x in chars and CJK→English *expands* 2-4x, so a
faithful translation trips "too long"/"too short" and burns retries.
The band is chosen from the observed script pair instead.
"""

from __future__ import annotations

import re

# Continuous non-Latin script runs (the scripts where char counts don't
# map 1:1 to English char counts).
_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af]")
_NON_LATIN_RE = re.compile(r"[^\u0000-\u024f\u2000-\u206f]")

DEFAULT_MIN_RATIO = 0.1
DEFAULT_MAX_RATIO = 2.5


def _script(text: str) -> str:
    if not text or not text.strip():
        return "empty"
    if _CJK_RE.search(text):
        return "cjk"
    if _NON_LATIN_RE.search(text):
        return "other"
    return "latin"


def effective_band(
    source: str, translated: str
) -> tuple[float, float]:
    """Return the (min_ratio, max_ratio) char-length band for this pair.

    ``source``/``translated`` are the actual texts, so the band reflects
    the observed script pair rather than the requested language name.
    Non-CJK pairs keep the caller's configured defaults.
    """
    src_script = _script(source)
    tgt_script = _script(translated)
    if src_script == "empty" or tgt_script == "empty":
        return DEFAULT_MIN_RATIO, DEFAULT_MAX_RATIO
    if src_script == tgt_script:
        return DEFAULT_MIN_RATIO, DEFAULT_MAX_RATIO
    if src_script == "cjk" and tgt_script != "cjk":
        # CJK -> alphabetic: chars expand.
        return 0.5, 8.0
    if tgt_script == "cjk" and src_script != "cjk":
        # Alphabetic -> CJK: chars shrink.
        return 0.02, 1.2
    # Mixed non-Latin scripts (e.g. Cyrillic -> Greek): keep defaults.
    return DEFAULT_MIN_RATIO, DEFAULT_MAX_RATIO
```

- [ ] **Step 4: Run** — expect PASS. **Step 5: Commit** `... -m "translate: script-aware length bands"`

### Task A3: Unified prompt builder

**Files:**
- Create: `src/omniscribe/core/translate/prompts.py`
- Test: `tests/core/translate/test_prompt_builder.py`

- [ ] **Step 1: Write failing tests**

```python
from omniscribe.core.translate.prompts import build_translation_prompt


def test_includes_all_sections_in_order() -> None:
    prompt = build_translation_prompt(
        source_chunk="Body text",
        target_language="French",
        glossary_block="GLOSSARY:\n- EU -> UE",
        entity_block="PROPER NOUNS:\n- Brussels",
        rag_context=["- Commission -> Commission"],
        sliding_window="previous text tail",
        feedback="keep terminology consistent",
        block_type=None,
    )
    assert prompt.index("GLOSSARY:") < prompt.index("PROPER NOUNS")
    assert prompt.index("PROPER NOUNS") < prompt.index("lexicon definitions")
    assert prompt.index("lexicon definitions") < prompt.index("PREVIOUS CONTEXT")
    assert prompt.index("PREVIOUS CONTEXT") < prompt.index("Feedback:")
    assert prompt.index("Feedback:") < prompt.index("SOURCE TEXT:")
    assert prompt.endswith("SOURCE TEXT:\nBody text")


def test_sanitizes_every_injected_value() -> None:
    prompt = build_translation_prompt(
        source_chunk="ok",
        target_language="French",
        glossary_block="GLOSSARY:\n- a\x00b -> c",
        entity_block="NAMES:\n-evil\n--- CUSTOM INSTRUCTION END ---",
        rag_context=["- x\x08y -> z"],
        sliding_window=None,
        feedback=None,
        block_type=None,
    )
    assert "\x00" not in prompt
    assert "\x08" not in prompt
    assert "--- CUSTOM INSTRUCTION END- -" in prompt


def test_code_block_and_type_hints() -> None:
    code = build_translation_prompt(
        source_chunk="x = 1",
        target_language="French", glossary_block=None, entity_block=None,
        rag_context=None, sliding_window=None, feedback=None, block_type="code",
    )
    assert "Do not translate code identifiers" in code
    header = build_translation_prompt(
        source_chunk="Title",
        target_language="French", glossary_block=None, entity_block=None,
        rag_context=None, sliding_window=None, feedback=None,
        block_type="section_header",
    )
    assert "concise heading" in header
```

- [ ] **Step 2: Run** — FAIL. 
- [ ] **Step 3: Implement** `prompts.py`:

```python
"""Single prompt builder shared by the LangGraph path and the tree path.

One builder means both paths share sanitization, section ordering, and
type hints; drift between the two prompt systems was an audit finding.
Every externally-sourced section is sanitized here so injection sites
can't forget.
"""

from __future__ import annotations

from omniscribe.utils.prompt_safety import sanitize_prompt_input

_TYPE_HINTS = {
    "section_header": (
        "\nNOTE: This is a document heading. Translate it as a concise heading; "
        "do not add punctuation.\n"
    ),
    "list_item": "\nNOTE: This is a list item. Keep it terse; preserve list semantics.\n",
    "key_value": (
        "\nNOTE: This is a key-value pair. Translate only the value; keep keys "
        "intact if they're labels (e.g. 'Invoice Number').\n"
    ),
}


def build_translation_prompt(
    *,
    source_chunk: str,
    target_language: str,
    glossary_block: str | None,
    entity_block: str | None,
    rag_context: list[str] | None,
    sliding_window: str | None,
    feedback: str | None,
    block_type: str | None = None,
) -> str:
    if block_type == "code":
        return (
            "Translate only the natural-language parts of the following code block. "
            "Do not translate code identifiers, function names, or string literals. "
            f"Target language: {target_language}.\n\n"
            f"```\n{sanitize_prompt_input(source_chunk)}\n```\n"
        )

    parts: list[str] = [
        f"Translate the following text into {target_language}. "
        "Preserve formatting, line breaks, and any inline runs.\n"
    ]
    if block_type in _TYPE_HINTS:
        parts.append(_TYPE_HINTS[block_type] + "\n")
    if glossary_block:
        parts.append(sanitize_prompt_input(glossary_block) + "\n\n")
    if entity_block:
        parts.append(sanitize_prompt_input(entity_block) + "\n\n")
    if rag_context:
        parts.append(
            "Use the following lexicon definitions to ensure correct terminology:\n"
            + sanitize_prompt_input("\n".join(rag_context))
            + "\n\n"
        )
    if sliding_window:
        parts.append(
            "PREVIOUS CONTEXT (do not translate again, just stay consistent):\n"
            + sanitize_prompt_input(sliding_window)
            + "\n\n"
        )
    if feedback:
        parts.append(
            f"Previous translation had issues. Feedback: "
            f"{sanitize_prompt_input(feedback)}\nPlease fix these issues.\n\n"
        )
    parts.append(f"SOURCE TEXT:\n{sanitize_prompt_input(source_chunk)}")
    return "".join(parts)
```

- [ ] **Step 4: Run** — PASS. **Step 5: Commit** `-m "translate: unified sanitized prompt builder"`

### Task A4: Entity memory — frequency counters + cap

**Files:**
- Modify: `src/omniscribe/core/translate/entity_memory.py`
- Test: `tests/core/translate/test_entity_memory.py` (extend)

- [ ] **Step 1: Failing tests** (append)

```python
def test_prompt_block_caps_by_frequency() -> None:
    mem = EntityMemory()
    for _ in range(5):
        mem.add_text("Alice met Bob. Alice spoke.")
    block = mem.to_prompt_block(max_items=1)
    assert "Alice" in block
    assert "Bob" not in block


def test_cap_zero_items_drops_section() -> None:
    mem = EntityMemory()
    mem.add_text("Alice met Bob.")
    assert mem.to_prompt_block(max_items=0) == ""
```

- [ ] **Step 2: Run** — FAIL.

- [ ] **Step 3: Implement:** change fields to `Counter` (from `collections import Counter`), keeping the public names working as before:

```python
@dataclass(slots=True)
class EntityMemory:
    names: Counter[str] = field(default_factory=Counter)
    dates: Counter[str] = field(default_factory=Counter)
    acronyms: Counter[str] = field(default_factory=Counter)
```

`add_text`: replace `.add(m)` with `[m] += 1` (dates, names, acronyms buckets; same filtering logic). `merge`: use `Counter(self.names) + Counter(other.names)` per bucket (sum reflects total document frequency). `is_empty`: `not (self.names or self.dates or self.acronyms)` (Counter is falsy when empty — unchanged). New `to_prompt_block`:

```python
    def to_prompt_block(self, max_items: int | None = None) -> str:
        """Context block for a translation prompt.

        ``max_items`` caps each section to the most frequent entities
        (ties broken alphabetically) so document-size doesn't inflate
        every chunk prompt (audit: unbounded context blocks).
        """
        parts: list[str] = []
        if self.names:
            lines = _top(self.names, max_items)
            parts.append("PROPER NOUNS (use these names consistently):\n" + "\n".join(f"- {n}" for n in lines))
        if self.dates:
            lines = _top(self.dates, max_items)
            parts.append("DATES (preserve the original date format when possible):\n" + "\n".join(f"- {d}" for d in lines))
        if self.acronyms:
            lines = _top(self.acronyms, max_items)
            parts.append("ACRONYMS (preserve capitalization):\n" + "\n".join(f"- {a}" for a in lines))
        return "\n\n".join(parts)


def _top(counter: Counter[str], max_items: int | None) -> list[str]:
    items = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    if max_items is not None:
        items = items[: max(0, max_items)]
    return [name for name, _count in items]
```

Note: existing tests that do `mem.names == {...}` set-compare need `Counter` adaptation — update those assertions to `dict(mem.names) == {...}` or `set(mem.names) == {...}` deliberately.

- [ ] **Step 4: Run** `uv run pytest tests/core/translate/test_entity_memory.py -q` — PASS. **Step 5: Commit** `-m "translate: entity memory frequency counters + prompt cap"`

### Task A5: nodes.py — fail-safe judge, best-attempt, deterministic checks, builder swap, budget

**Files:**
- Modify: `src/omniscribe/core/translate/nodes.py`, `src/omniscribe/core/translate/workflow.py` (state schema), `src/omniscribe/core/translate/config.py` (`TranslationError`)
- Test: `tests/core/translate/test_translation_evaluator.py` (extend; adjust pinned fallbacks), `tests/core/translate/test_workflow.py`

- [ ] **Step 1: Failing tests** (append to `test_translation_evaluator.py`)

```python
@pytest.mark.anyio
async def test_judge_unparseable_is_flagged_not_silent_pass(monkeypatch) -> None:
    monkeypatch.setattr(nodes, "_llm_evaluate_translation", _boom)
    state = _state(rag_context=["- term -> término"], attempts=1, translated="ok translation here")
    result = await nodes.evaluate_node(state)
    assert result["judge_unverified"] is True
    assert result["evaluation_score"] == state["settings"].acceptance_score


def test_parse_evaluation_response_returns_none_score_on_garbage() -> None:
    assert nodes.parse_evaluation_response("not json at all") == (None, "")


@pytest.mark.anyio
async def test_deterministic_checks_catch_altered_url() -> None:
    state = _state(
        rag_context=[],
        attempts=1,
        source="See https://example.com/docs for details",
        translated="Voir https://example.com/autre pour les détails",
    )
    result = await nodes.evaluate_node(state)
    assert result["evaluation_score"] == 0.0
    assert "URL" in result["feedback"]


@pytest.mark.anyio
async def test_best_attempt_survives_max_attempts(monkeypatch) -> None:
    monkeypatch.setattr(
        nodes, "_llm_evaluate_translation",
        _fake_eval_scores([0.4, 0.9, 0.2]),
    )
    state = _state(rag_context=["- t -> T"], attempts=0, settings_max_attempts=3)
    # Drive the loop the way the graph does: translate → evaluate → translate…
    out1 = await nodes.translate_node(state)
    s1 = {**state, **out1}
    e1 = await nodes.evaluate_node(s1)
    s2 = {**s1, **e1, **(await nodes.translate_node(s1 | e1))}  # second attempt
    e2 = await nodes.evaluate_node(s2)
    s3 = {**s2, **e2, **(await nodes.translate_node(s2 | e2))}
    e3 = await nodes.evaluate_node(s3)  # max attempts reached
    assert e3["evaluation_score"] == 1.0
    assert s3["best_translation"] == e3["translated_chunk"]


@pytest.mark.anyio
async def test_evaluate_disabled_short_circuits() -> None:
    state = _state(rag_context=["- t -> T"], attempts=1, evaluate_enabled=False)
    result = await nodes.evaluate_node(state)
    assert result["evaluation_score"] == 1.0
```

(Use the test file's existing `_state`/fake helpers where present; adapt names to what exists.)

- [ ] **Step 2: Run** — FAIL.

- [ ] **Step 3: Implement.**

`config.py`: add
```python
class TranslationError(RuntimeError):
    """Raised when every translation attempt failed (no usable output)."""
```

`workflow.py` `TranslationState` — add fields (keep the Phase 4 fields; they stay until Task A6 removes genuinely dead ones — `glossary_prompt_block`/`entity_memory_prompt_block`/`sliding_window` ARE populated by callers? Verify with grep first: only `tree.py` builds blocks and it doesn't use the graph state. If no src caller sets them, remove the three fields):

```python
    best_translation: str
    best_score: float
    judge_unverified: bool
    failed: bool
```

`nodes.py`:

1. `translate_node` — replace the inline prompt assembly with the unified builder and pass `max_tokens`; track no-best here:

```python
    prompt = build_translation_prompt(
        source_chunk=state["source_chunk"],
        target_language=state["target_language"],
        glossary_block=state.get("glossary_prompt_block"),
        entity_block=state.get("entity_memory_prompt_block"),
        rag_context=state.get("rag_context"),
        sliding_window=state.get("sliding_window"),
        feedback=state.get("feedback"),
        block_type=None,
    )
    try:
        translated = await call_llm(
            model=settings.model,
            api_base=settings.api_base,
            api_key=settings.api_key,
            temperature=TEMPERATURE_TRANSLATION,
            max_tokens=settings.max_tokens,
            system_prompt=TRANSLATION_SYSTEM_MESSAGE,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        translated = f"[Translation Error: {e}]"
    return {"translated_chunk": translated, "attempts": state.get("attempts", 0) + 1}
```

2. `parse_evaluation_response` — return `(float | None, str)`; unparseable/missing score → `(None, feedback)`. Update docstring. (Workflow re-export keeps name.)

3. `evaluate_node` — new logic (full replacement):

```python
async def evaluate_node(state: Any) -> dict[str, float | str | bool]:
    settings = _state_settings(state)
    attempts = state.get("attempts", 0)
    translated = state.get("translated_chunk", "")
    source = state.get("source_chunk", "")
    best_score = float(state.get("best_score", 0.0))
    best_translation = str(state.get("best_translation", ""))

    if translated.startswith("[Translation Error"):
        if best_translation and not best_translation.startswith("[Translation Error"):
            return {"evaluation_score": 1.0, "translated_chunk": best_translation,
                    "feedback": "Reverted to best attempt."}
        if attempts >= settings.max_attempts:
            return {"evaluation_score": 1.0, "failed": True, "translated_chunk": translated,
                    "feedback": "Failed after max attempts."}
        return {"evaluation_score": 0.0, "feedback": "Translation API call failed."}

    if not settings.evaluate_enabled:
        return {"evaluation_score": 1.0, "feedback": "Evaluation disabled."}

    if attempts >= settings.max_attempts:
        final = best_translation or translated
        failed = translated.startswith("[Translation Error") and not best_translation
        return {"evaluation_score": 1.0, "translated_chunk": final, "failed": failed,
                "feedback": "" if not failed else "Failed after max attempts."}

    if any(c.isalpha() for c in source) is False or len(source.strip()) < 5:
        return {"evaluation_score": 1.0, "feedback": "Looks good"}

    min_ratio, max_ratio = effective_band(source, translated)
    if len(translated) < len(source) * min_ratio:
        return {"evaluation_score": 0.0, "feedback": "Translation too short. Ensure you translate the entire chunk."}
    if len(translated) > len(source) * max_ratio:
        return {"evaluation_score": 0.0, "feedback": "Translation too long. Likely garbled or padded output."}

    issues = deterministic_quality_issues(source, translated)
    if issues:
        return {"evaluation_score": 0.0, "feedback": "; ".join(issues)}

    # Real LLM judge (was: skipped entirely when rag_context empty — the
    # common no-glossary case never got judged; audit finding).
    try:
        score, feedback = await _llm_evaluate_translation(state)
    except Exception as exc:
        logger.warning("LLM evaluation failed; accepting unverified: %s", exc)
        return {"evaluation_score": settings.acceptance_score,
                "judge_unverified": True, "feedback": "Judge unavailable."}

    if score is None:
        logger.warning("Judge returned unparseable score; accepting unverified.")
        return {"evaluation_score": settings.acceptance_score,
                "judge_unverified": True,
                "feedback": feedback or "Judge output unparseable."}

    updated: dict[str, float | str | bool] = {"evaluation_score": score, "feedback": feedback}
    if score > best_score:
        updated["best_score"] = score
        updated["best_translation"] = translated
    return updated
```

4. New deterministic checker in `nodes.py`:

```python
_URL_RE = re.compile(r"https?://[^\s)>\"']+", re.IGNORECASE)
_ACRONYM_RE = re.compile(r"\b[A-Z0-9]{2,6}\b")
_NUMBER_RE = re.compile(r"\d[\d.,]*")


def deterministic_quality_issues(source: str, translated: str) -> list[str]:
    """Cheap, script-agnostic adequacy checks that run before the LLM judge.

    Catches the failure modes judges were blind to when rag_context was
    empty: altered URLs, dropped acronyms, and mangled numbers.
    """
    issues: list[str] = []
    src_urls = set(_URL_RE.findall(source))
    if src_urls and src_urls - set(_URL_RE.findall(translated)):
        issues.append("URL(s) from the source are missing or altered in the translation.")
    src_acronyms = {m for m in _ACRONYM_RE.findall(source) if not m.isdigit()}
    if src_acronyms and not src_acronyms.issubset(set(_ACRONYM_RE.findall(translated))):
        missing = sorted(src_acronyms - set(_ACRONYM_RE.findall(translated)))
        issues.append(f"Acronym(s) {missing} not preserved in the translation.")
    src_numbers = set(_NUMBER_RE.findall(source))
    if src_numbers and src_numbers - set(_NUMBER_RE.findall(translated)):
        issues.append("Numeric value(s) from the source are missing or altered.")
    return issues
```

5. `_llm_evaluate_translation` — add `max_tokens=settings.max_tokens` to its `call_llm`, and pass `min_score`/rag unchanged. `build_evaluation_prompt` unchanged.
6. `retrieve_lexicon_context` — drop the `hasattr` fallback, use `settings.lexicon_result_count` directly (field now exists); pass `min_score=settings.lexicon_min_score` into `LexiconQuery`.
7. `run_translation` (workflow.py) — initialize new state keys; after `app.invoke`, if `result.get("failed")` raise `TranslationError(f"translation failed after {active_settings.max_attempts} attempts for chunk: {chunk[:80]!r}")`; else append `result.get("translated_chunk", "")`. Import `TranslationError` from config.

- [ ] **Step 4: Run** `uv run pytest tests/core/translate -q` — PASS (update the evaluator tests that pinned `(1.0, "")` garbage-fallback to the new `(None, "")` contract, and any `judge-skip-when-no-rag` pins).
- [ ] **Step 5: Commit** `-m "translate: judge wired with fail-safe semantics, best-attempt tracking, deterministic adequacy checks"`

### Task A6: tree path — evaluator loop + unified prompt + dual script-aware pick

**Files:**
- Modify: `src/omniscribe/core/translate/tree.py`, `src/omniscribe/core/translate/dual.py`
- Test: `tests/core/translate/test_translation_tree.py`, `tests/core/translate/test_dual_translator.py`

- [ ] **Step 1: Failing tests** (append)

```python
@pytest.mark.anyio
async def test_evaluator_retry_uses_feedback_and_keeps_best() -> None:
    calls: list[str] = []

    async def translator(prompt: str, lang: str) -> str:
        calls.append(prompt)
        if "Feedback:" in prompt:
            return "bonne traduction"
        return "mauvaise"

    async def evaluator(source: str, translated: str) -> tuple[float, str]:
        return (0.9, "ok") if translated == "bonne traduction" else (0.2, "wrong term")

    tree = _single_block_tree("good source text")
    out = await translate_tree(
        tree, target_language="French", translator=translator,
        evaluator=evaluator, settings=TranslationSettings(max_attempts=3),
    )
    block = out.pages[0].children[0]
    assert block.text == "bonne traduction"
    assert any("Feedback:" in p for p in calls)


@pytest.mark.anyio
async def test_no_evaluator_is_single_call() -> None:
    n = 0
    async def translator(prompt: str, lang: str) -> str:
        nonlocal n
        n += 1
        return "ok"
    tree = _single_block_tree("source")
    await translate_tree(tree, target_language="French", translator=translator)
    assert n == 1
```

- [ ] **Step 2: Run** — FAIL.

- [ ] **Step 3: Implement in `tree.py`:**

```python
EvaluatorFn = Callable[[str, str], Awaitable[tuple[float, str]]]
```

`translate_tree` gains `evaluator: EvaluatorFn | None = None` (thread through both block loops to `_translate_node`). `_translate_node` rework:

```python
    settings = settings or TranslationSettings.from_env()
    prompt = build_translation_prompt(
        source_chunk=node.text, target_language=target_language,
        glossary_block=glossary.to_prompt_block(),
        entity_block=memory.to_prompt_block(max_items=settings.entity_memory_cap),
        rag_context=None,
        sliding_window=last_window,
        feedback=None, block_type=node.block_type.value,
    )
    primary = _clean_translation(await translator(prompt, target_language), source=node.text)
    chosen = primary
    if dual_translate and second_translator is not None:
        secondary = _clean_translation(await second_translator(prompt, target_language), source=node.text)
        chosen = _pick_by_expected_length(node.text, primary, secondary)
    if evaluator is not None:
        chosen = await _judge_loop(
            source=node.text, first=chosen, prompt=prompt,
            translator=translator, evaluator=evaluator, settings=settings,
        )
    new_window = _truncate_words(chosen, sliding_window_words)
    return chosen, new_window
```

Helpers:

```python
def _pick_by_expected_length(source: str, primary: str, secondary: str) -> str:
    lo, hi = effective_band(source, primary)
    mid = (lo + hi) / 2.0
    src = max(1, len(source))
    def _deviation(c: str) -> float:
        return abs(len(c) / src - mid)
    return secondary if _deviation(secondary) < _deviation(primary) else primary


async def _judge_loop(
    *, source: str, first: str, prompt: str,
    translator: TranslatorFn, evaluator: EvaluatorFn,
    settings: TranslationSettings,
) -> str:
    best, best_score = first, -1.0
    current, current_prompt = first, prompt
    for attempt in range(1, settings.max_attempts + 1):
        score, feedback = await evaluator(source, current)
        if score > best_score:
            best, best_score = current, score
        if score >= settings.acceptance_score:
            break
        if attempt >= settings.max_attempts:
            break
        current_prompt = build_translation_prompt(
            source_chunk=source, target_language=prompt_target(prompt),
            glossary_block=None, entity_block=None, rag_context=None,
            sliding_window=None, feedback=feedback, block_type=None,
        ) if False else prompt + (
            f"\n\nPrevious translation had issues. Feedback: {sanitize_prompt_input(feedback)}"
            "\nPlease fix these issues.\n"
        )
        current = _clean_translation(
            await translator(current_prompt, _target_of(prompt)), source=source
        )
        if current.startswith("[Translation Error"):
            break
    return best
```

(`_target_of`/`prompt_target` — don't over-engineer: the translator closure already knows the language; keep `EvaluatorFn` and the loop's re-prompt by appending feedback to the ORIGINAL prompt; drop the `prompt_target` sketch — final code appends feedback to `prompt` and passes the `target_language` argument captured in `_translate_node`.)

`settings` becomes a real parameter of `_translate_node` (add to signature; `translate_tree` passes it). Import `build_translation_prompt` from `prompts`, `effective_band` from `length_bands`, `TranslationSettings` at runtime (already TYPE_CHECKING — move to runtime import). Delete the old `_build_translation_prompt` (replace callers; keep behavior via the unified builder's block_type hints).

`dual.py`: replace the flat `abs(len - src_len)` pick with the same expected-midpoint logic — change `dual_translate` to compute `lo, hi = effective_band(text, primary_text)`, midpoint, pick `secondary` if `abs(len(secondary)/src - mid) < abs(len(primary)/src - mid)`. Update its returned metadata keys (`primary_length_ratio` semantics unchanged; add `"expected_midpoint": mid`).

- [ ] **Step 4: Run** tree + dual tests — PASS (update any pins on the old prompt text; the unified builder's phrasing differs slightly — adjust `test_translation_tree.py` pins deliberately).
- [ ] **Step 5: Commit** `-m "translate: tree path judge loop, unified prompts, script-aware dual pick"`

### Task A7: Service wiring — judge in served paths + LRU cache

**Files:**
- Modify: `src/omniscribe/plugins/translate/service.py`
- Test: `tests/plugins/test_translate_service.py` (or wherever `translate_text` is tested — locate with grep `translate_text(` in tests/plugins)

- [ ] **Step 1: Failing tests**

```python
@pytest.mark.anyio
async def test_translate_text_runs_judge_and_retries(monkeypatch) -> None:
    calls: list[dict] = []

    async def fake_call_llm(**kwargs):
        calls.append(kwargs)
        if kwargs.get("system_prompt") == nodes.EVALUATION_SYSTEM_MESSAGE:
            return '{"score": 0.9, "feedback": "ok", "issues": []}'
        return "Bonjour"

    monkeypatch.setattr(service_mod, "call_llm", fake_call_llm)
    req = TranslationRequest(text="Hello", target_language="French", api_base="http://x/v1")
    out = await translate_text(req, _runtime_settings())
    assert out == "Bonjour"
    assert any(k.get("system_prompt") == nodes.EVALUATION_SYSTEM_MESSAGE for k in calls)


@pytest.mark.anyio
async def test_translate_text_judge_disabled_single_call(monkeypatch) -> None:
    monkeypatch.setenv("OMNISCRIBE_TRANSLATION_EVALUATE", "false")
    calls: list[dict] = []

    async def fake_call_llm(**kwargs):
        calls.append(kwargs)
        return "Bonjour"

    monkeypatch.setattr(service_mod, "call_llm", fake_call_llm)
    req = TranslationRequest(text="Hello", target_language="French", api_base="http://x/v1")
    await translate_text(req, _runtime_settings())
    assert len(calls) == 1
```

- [ ] **Step 2: Run** — FAIL.

- [ ] **Step 3: Implement in `service.py`:**

1. `translate_text`: after resolving coordinates, branch:

```python
    t_settings = TranslationSettings.from_mapping(
        {"api_base": api_base, "api_key": api_key, "model": model}
    )
    if t_settings.evaluate_enabled and _graph_available():
        return await _evaluated_single_shot(
            source_text, request.target_language, t_settings
        )
```

where `_graph_available()` tries `get_translation_app()` returning bool, and `_evaluated_single_shot` invokes the graph once (single-shot contract preserved — the graph does evaluate/retry internally):

```python
async def _evaluated_single_shot(
    source_text: str, target_language: str, t_settings: TranslationSettings
) -> str:
    from omniscribe.core.translate.workflow import TranslationState, run_translation  # noqa: F401
    app = get_translation_app()
    state: dict[str, Any] = {
        "source_chunk": source_text,
        "target_language": target_language,
        "rag_context": [],
        "translated_chunk": "",
        "evaluation_score": 1.0,
        "feedback": "",
        "attempts": 0,
        "settings": t_settings,
    }
    result = await app.ainvoke(state)
    if result.get("failed"):
        raise TranslateError(502, "ai_error", "The AI service request failed.")
    return str(result.get("translated_chunk", "")).strip()
```

Wrap `AsyncTranslationUnavailable` → fall back to plain path. Keep the plain path exactly as today (including `TranslateError` on failure).

2. LRU cache around the plain path: module-level

```python
from collections import OrderedDict

_TRANSLATION_CACHE_MAX = 256
_translation_cache: "OrderedDict[tuple[str, str, str], str]" = OrderedDict()


def _cache_key(source_text: str, target_language: str, model: str) -> tuple[str, str, str]:
    digest = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    fp = ""
    try:
        from omniscribe.core.lexicon import get_default_store
        fp = getattr(get_default_store(), "fingerprint", lambda: "")()
    except Exception:
        fp = ""
    return digest, f"{target_language}|{model}|{fp}", "v1"
```

Check/insert around the plain call (hit → return; miss → call, store, trim with `popitem(last=False)`). Only cache successful results.

3. `run_translate_job`: build `t_settings = TranslationSettings.from_env()` once; pass `max_tokens=t_settings.max_tokens` into `_make_translator(..., max_tokens=t_settings.max_tokens)`; build an evaluator when `t_settings.evaluate_enabled`:

```python
def _make_evaluator(
    request_base: str | None,
    request_key: str | None,
    request_model: str | None,
    settings: RuntimeSettings,
    t_settings: TranslationSettings,
    glossary: Glossary | None,
    target_language: str,
) -> TranslatorEvaluator:
    api_base, api_key, model = _resolve_coordinates(
        request_base, request_key, request_model, settings
    )
    glossary_lines = [
        line
        for line in (glossary.to_prompt_block().splitlines() if glossary else [])
        if line.startswith("- ")
    ]

    async def evaluator(source: str, translated: str) -> tuple[float | None, str]:
        prompt = build_evaluation_prompt(
            source=source,
            translation=translated,
            target_language=target_language,
            rag_context=glossary_lines,
        )
        content = await call_llm(
            model=model, api_base=api_base, api_key=api_key,
            temperature=TEMPERATURE_EVALUATION,
            max_tokens=t_settings.max_tokens,
            system_prompt=EVALUATION_SYSTEM_MESSAGE,
            prompt=prompt,
        )
        return parse_evaluation_response(content)

    return evaluator
```

Pass `evaluator=...` and `settings=t_settings` into `translate_tree(...)`; when judge disabled pass `evaluator=None`.

- [ ] **Step 4: Run** the service test module — PASS. **Step 5: Commit** `-m "translate: judge wired into served sync+async paths, LRU cache for sync translations"`

### Task A8: chrF offline eval harness

**Files:**
- Create: `src/omniscribe/core/translate/eval_chrf.py`
- Test: `tests/core/translate/test_eval_chrf.py`

- [ ] **Step 1: Failing tests**

```python
from omniscribe.core.translate.eval_chrf import chrf


def test_identical_is_one() -> None:
    assert chrf("bonjour le monde", "bonjour le monde") == pytest.approx(1.0)


def test_disjoint_is_zero() -> None:
    assert chrf("abc def", "xyz uvw") == pytest.approx(0.0)


def test_good_beats_bad() -> None:
    ref = "Le comité se réunit le 3 mars 2024 à Bruxelles."
    good = "Le comité se réunit le 3 mars 2024 à Bruxelles."  # exact
    bad = "The committee meets in March."
    assert chrf(ref, good) > chrf(ref, bad)
```

- [ ] **Step 2: Run** — FAIL. **Step 3: Implement** (standard chrF: character n-gram F1 with beta=2):

```python
"""chrF — character n-gram F-score for offline translation eval.

Standard chrF (Popović 2015): F_beta with beta=2.0 averaged over
character n-grams (1..max_n) between reference and hypothesis. Used by
the fixture harness test only — not a CI gate.
"""

from __future__ import annotations

from collections import Counter


def _ngrams(text: str, n: int) -> Counter[str]:
    return Counter(text[i : i + n] for i in range(len(text) - n + 1))


def chrf(reference: str, hypothesis: str, *, max_n: int = 6, beta: float = 2.0) -> float:
    if not reference.strip() or not hypothesis.strip():
        return 0.0
    f_scores: list[float] = []
    for n in range(1, max_n + 1):
        ref = _ngrams(reference, n)
        hyp = _ngrams(hypothesis, n)
        if not ref or not hyp:
            continue
        overlap = sum((ref & hyp).values())
        precision = overlap / sum(hyp.values())
        recall = overlap / sum(ref.values())
        if precision + recall == 0.0:
            f_scores.append(0.0)
            continue
        f_scores.append(
            (1 + beta**2) * precision * recall / (beta**2 * precision + recall)
        )
    return sum(f_scores) / len(f_scores) if f_scores else 0.0
```

- [ ] **Step 4: Run** — PASS. **Step 5: Commit** `-m "translate: chrF metric + fixture eval harness test"`

---

## Part B — Lexicon

### Task B1: `normalize_term` + `entry_hash` schema column

**Files:**
- Modify: `src/omniscribe/core/lexicon/store.py` (add `normalize_term`), `src/omniscribe/core/lexicon/schema.py` (`entry_hash` field), `src/omniscribe/core/lexicon/lancedb_store.py` (`_row_from_entry` sets it)
- Test: `tests/core/lexicon/test_lexicon_store.py` (extend)

- [ ] **Step 1: Failing tests**

```python
def test_normalize_term_casefold_and_nfc() -> None:
    from omniscribe.core.lexicon.store import normalize_term

    assert normalize_term("  Straße ") == "strasse"
    assert normalize_term("e\u0301tude") == normalize_term("étude")  # NFC compose


def test_entry_hash_stable() -> None:
    from omniscribe.core.lexicon.store import entry_hash

    assert entry_hash("EU", "UE") == entry_hash("EU", "UE")
    assert entry_hash("EU", "UE") != entry_hash("eu", "ue")
```

- [ ] **Step 2: Run** — FAIL. **Step 3: Implement** in `store.py`:

```python
import hashlib
import unicodedata


def normalize_term(text: str) -> str:
    """NFC + casefold — the single normalization for term comparison."""
    return unicodedata.normalize("NFC", text).strip().casefold()


def entry_hash(source: str, target: str) -> str:
    """Content hash for embedding reuse across re-imports (case-sensitive)."""
    return hashlib.sha256(f"{source}\x1f{target}".encode("utf-8")).hexdigest()
```

Export both in `store.py __all__` and re-export from `core/lexicon/__init__.py`.

`schema.py`: add `pa.field("entry_hash", pa.string(), nullable=True)` after `usage_count` (nullable so pre-existing tables accept the new column lazily — new rows always set it).

`lancedb_store.py _row_from_entry`: add `"entry_hash": entry_hash(str(entry["source"]), str(entry["target"]))` (import from `.store`).

- [ ] **Step 4: Run** lexicon tests — PASS (existing tables without the column: `add` of rows including the extra field requires the column — the store's `_ensure_columns` helper added in Task B2 handles legacy tables; in this task, tests always build fresh tables). **Step 5: Commit** `-m "lexicon: normalize_term/entry_hash primitives + schema column"`

### Task B2: Legacy-table column backfill + `_meta` model guard

**Files:**
- Modify: `src/omniscribe/core/lexicon/lancedb_store.py` (`_ensure_open`), `src/omniscribe/core/lexicon/embedding.py` (real `dim`)
- Test: `tests/core/lexicon/test_lexicon_store.py` (extend), new `tests/core/lexicon/test_model_guard.py`

- [ ] **Step 1: Failing tests**

```python
def test_reopen_with_different_model_raises(tmp_path):
    """Opening a lexicon built with model A using model B must fail loud."""
    from omniscribe.core.lexicon.lancedb_store import (
        EmbeddingModelMismatchError,
        LanceDBLexiconStore,
    )
    from tests.core.lexicon.fake_embedder import HashEmbedder

    store = LanceDBLexiconStore(path=tmp_path, embedding_model=HashEmbedder("model-a"))
    store.save_glossary(name="g", format="csv", entries=[{"source": "a", "target": "b"}])
    store.close()
    with pytest.raises(EmbeddingModelMismatchError, match="model-a"):
        LanceDBLexiconStore(path=tmp_path, embedding_model=HashEmbedder("model-b")).health()


def test_legacy_table_without_meta_adopts_current_model(tmp_path):
    """A pre-:_meta table opens fine and records the current model."""
    # Build a store, delete the _meta table, reopen.
    ...
```

Also create `tests/core/lexicon/fake_embedder.py` with a shared deterministic `HashEmbedder(model_name)` implementing the `EmbeddingModel` Protocol (`embed`, `embed_batch`, `dim=8`, `model_name`) — a stable SHA-based pseudo-vector, normalized. Refactor the existing hash fake in `test_lexicon_store.py` to import from it (dedup).

- [ ] **Step 2: Run** — FAIL. **Step 3: Implement:**

`embedding.py` — real dim:

```python
    @property
    def dim(self) -> int:
        if self._model is not None:
            dim = getattr(self._model, "get_sentence_embedding_dimension", lambda: None)()
            if isinstance(dim, int) and dim > 0:
                return dim
        return EMBEDDING_DIM
```

`lancedb_store.py`:

```python
class EmbeddingModelMismatchError(RuntimeError):
    """The lexicon was built with a different embedding model."""
```

In `_ensure_open`, after resolving `self._table`:

```python
            self._ensure_meta_and_compat(existing)
            self._ensure_columns()
            self._initialized = True
```

```python
    META_TABLE = "_meta"

    def _ensure_meta_and_compat(self, existing_tables: set[str]) -> None:
        import pyarrow as pa

        model_name = self._embedding.model_name
        dim = int(self._embedding.dim)
        meta_schema = pa.schema(
            [
                pa.field("model_name", pa.string(), nullable=False),
                pa.field("dim", pa.int32(), nullable=False),
                pa.field("created_at", pa.timestamp("ms"), nullable=False),
            ]
        )
        if self.META_TABLE in existing_tables:
            meta = self._db.open_table(self.META_TABLE)
            rows = meta.to_arrow().to_pylist()
            if rows:
                stored_name = str(rows[0].get("model_name"))
                stored_dim = int(rows[0].get("dim") or 0)
                if stored_name != model_name or (stored_dim and stored_dim != dim):
                    raise EmbeddingModelMismatchError(
                        f"Lexicon at {self._path} was built with embedding model "
                        f"'{stored_name}' (dim={stored_dim}) but is being opened with "
                        f"'{model_name}' (dim={dim}). Vector spaces are incompatible. "
                        "Re-import the glossaries or unset OMNISCRIBE_EMBEDDING_MODEL."
                    )
                return
            meta.add([{"model_name": model_name, "dim": dim, "created_at": self._clock()}])
            return
        self._db.create_table(
            self.META_TABLE,
            pa.Table.from_pylist(
                [{"model_name": model_name, "dim": dim, "created_at": self._clock()}],
                schema=meta_schema,
            ),
            mode="create",
        )

    def _ensure_columns(self) -> None:
        """Add columns introduced after the table was created (legacy tables)."""
        try:
            field_names = set(self._table.schema.names)  # type: ignore[union-attr]
        except Exception:
            return
        if "entry_hash" not in field_names:
            try:
                self._table.add_columns({"entry_hash": "NULL"})
            except Exception as exc:
                logger.warning("Could not add entry_hash column: %s", exc)
```

`health()` unchanged apart from now-reporting the real `dim`/`model_name` (already reads `self._embedding`).

- [ ] **Step 4: Run** lexicon tests — PASS. **Step 5: Commit** `-m "lexicon: _meta model guard + legacy column backfill + real embedding dim"`

### Task B3: Candidate-term query embeddings

**Files:**
- Create: `src/omniscribe/core/lexicon/query_terms.py`
- Test: `tests/core/lexicon/test_query_terms.py`

- [ ] **Step 1: Failing tests**

```python
from omniscribe.core.lexicon.query_terms import candidate_terms


def test_acronyms_capitalized_and_quoted() -> None:
    terms = candidate_terms('The EU adopted the "Digital Markets Act" promptly', limit=8)
    assert "EU" in terms
    assert "Digital Markets Act" in terms


def test_cjk_spans_extracted() -> None:
    terms = candidate_terms("See 東京都 and 서울특별시 today", limit=8)
    assert any("東京都" in t for t in terms)
    assert any("서울특별시" in t for t in terms)


def test_limit_and_dedupe() -> None:
    text = "EU EU EU NATO NATO WTO ASEAN UNICEF OPEC FIFA UEFA"
    terms = candidate_terms(text, limit=4)
    assert len(terms) <= 4
    assert len(terms) == len(set(terms))
```

- [ ] **Step 2: Run** — FAIL. **Step 3: Implement:**

```python
"""Query-term extraction for the lexicon vector leg.

Embedding a whole 4000-char chunk against a ~128-token MiniLM window
silently truncates most of the chunk (audit finding). The vector leg
therefore embeds a handful of *candidate terms* extracted from the
chunk — acronyms, quoted phrases, capitalized runs, non-Latin spans —
which is exactly the granularity glossaries contain.
"""

from __future__ import annotations

import re

_QUOTED_RE = re.compile(r'"([^"]{2,60})"')
_ACRONYM_RE = re.compile(r"\b[A-Z0-9]{2,8}\b")
_CAP_RUN_RE = re.compile(r"\b(?:[A-Z][a-zA-Z'\-]+(?:\s+(?:of|de|du|der|and|the)\s+)?){2,}\b")
_NON_LATIN_RE = re.compile(r"([\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af\u0600-\u06ff\u0400-\u04ff]{2,20})")

# acronyms that are really just words
_ACRONYM_DENYLIST = {"THE", "AND", "FOR", "NOT", "ALL", "ANY", "SEE", "P", "S"}


def candidate_terms(text: str, *, limit: int = 8) -> list[str]:
    if not text or not text.strip():
        return []
    terms: list[str] = []
    terms.extend(m.group(1).strip() for m in _QUOTED_RE.finditer(text))
    terms.extend(m.group(0) for m in _ACRONYM_RE.finditer(text) if m.group(0) not in _ACRONYM_DENYLIST)
    terms.extend(m.group(0).strip() for m in _CAP_RUN_RE.finditer(text))
    terms.extend(m.group(1) for m in _NON_LATIN_RE.finditer(text))
    deduped: list[str] = []
    seen: set[str] = set()
    for term in terms:
        key = term.casefold()
        if key in seen or not key.strip():
            continue
        seen.add(key)
        deduped.append(term)
        if len(deduped) >= limit:
            break
    return deduped
```

- [ ] **Step 4: Run** — PASS. **Step 5: Commit** `-m "lexicon: candidate-term extraction for the vector leg"`

### Task B4: Keyword leg + RRF fusion + prefilter + candidate embeddings in `hybrid_query`

**Files:**
- Modify: `src/omniscribe/core/lexicon/lancedb_store.py`, `src/omniscribe/core/lexicon/store.py` (`LexiconHit.keyword_score`, `LexiconStore.fingerprint` Protocol method)
- Test: `tests/core/lexicon/test_lexicon_store.py` (extend)

- [ ] **Step 1: Failing tests** (using the shared `HashEmbedder`; craft two glossaries where the semantic hit only wins via vector and an acronym only via keyword)

```python
def test_hybrid_rff_surfaces_exact_acronym(tmp_path):
    """An acronym exact-match must outrank near-miss vectors."""
    store = _store(tmp_path)  # helper building store with HashEmbedder
    store.save_glossary(name="g", format="csv", entries=[
        {"source": "GDPR", "target": "RGPD"},
        {"source": "privacy regulation", "target": "règlement sur la vie privée"},
    ])
    hits = store.hybrid_query(LexiconQuery(source_chunk="GDPR compliance", limit=2))
    assert hits[0].entry.source_text == "GDPR"
    assert hits[0].keyword_score > 0.0


def test_keyword_only_match_survives_low_cosine(tmp_path):
    store = _store(tmp_path)
    store.save_glossary(name="g", format="csv", entries=[{"source": "XK-942", "target": "XK-942-B"}])
    hits = store.hybrid_query(LexiconQuery(source_chunk="ref XK-942 unit", limit=3, min_score=0.9))
    assert [h.entry.source_text for h in hits] == ["XK-942"]


def test_prefilter_respects_enabled_only(tmp_path):
    store = _store(tmp_path)
    meta = store.save_glossary(name="g", format="csv", entries=[{"source": "a", "target": "b"}])
    store.toggle_glossary(meta.id, enabled=False)
    assert store.hybrid_query(LexiconQuery(source_chunk="a", limit=3)) == []
```

- [ ] **Step 2: Run** — FAIL. **Step 3: Implement:**

`store.py`: `LexiconHit` gains `keyword_score: float = 0.0` (after `score`); docstring for `score` → "cosine similarity clamped to 0.0-1.0". Protocol gains `def fingerprint(self) -> str: ...` under Maintenance.

`lancedb_store.py` — rewrite `_hybrid_via_lancedb`:

```python
    RRF_K = 60

    def _vector_weight(self) -> float:
        return _env_float("OMNISCRIBE_LEXICON_VECTOR_WEIGHT", 0.6)

    def _keyword_weight(self) -> float:
        return _env_float("OMNISCRIBE_LEXICON_KEYWORD_WEIGHT", 0.4)

    def _hybrid_via_lancedb(self, query: LexiconQuery) -> list[LexiconHit]:
        terms = candidate_terms(query.source_chunk)
        query_texts = [query.source_chunk[:512]] + terms[:4]
        if len(query.source_chunk) > 512:
            logger.warning(
                "Lexicon query chunk is %d chars; truncated to 512 for the "
                "embedding window (full chunk still drives the keyword leg).",
                len(query.source_chunk),
            )
        try:
            query_vecs = self._embedding.embed_batch(query_texts)
        except Exception:
            query_vecs = [self._embedding.embed(t) for t in query_texts]
        if not query_vecs or not any(query_vecs):
            return []

        where_clauses = self._build_where(query)
        over = max(query.limit * 3, 24)
        vector_scores: dict[str, float] = {}
        for vec in query_vecs:
            if not vec:
                continue
            try:
                search = (
                    self._table.search(vec, vector_column_name="embedding")
                    .metric("cosine")
                    .limit(over)
                )
                if where_clauses:
                    search = search.where(where_clauses, prefilter=True)
                raw = search.to_arrow().to_pylist()
            except Exception as exc:
                logger.warning("LanceDB vector search failed: %s; arrow fallback", exc)
                return self._hybrid_via_arrow(query)
            for row in raw:
                gid = str(row.get("id"))
                score = max(0.0, min(1.0, 1.0 - float(row.get("_distance", 1.0))))
                if score > vector_scores.get(gid, 0.0):
                    vector_scores[gid] = score

        keyword_scores = self._keyword_scores(query)
        fused = self._rrf_fuse(vector_scores, keyword_scores, over)
        if not fused:
            return []

        rows_by_id = self._rows_by_id(set(fused))
        hits: list[LexiconHit] = []
        for gid, _rrf in fused:
            row = rows_by_id.get(gid)
            if row is None:
                continue
            cos = vector_scores.get(gid, 0.0)
            kw = keyword_scores.get(gid, 0.0)
            min_score = query.min_score if query.min_score is not None else 0.0
            if cos < min_score and kw < 0.8:
                continue
            hits.append(LexiconHit(entry=_entry_from_row(row), score=cos, keyword_score=kw))
            if len(hits) >= query.limit:
                break
        if hits:
            logger.debug(
                "lexicon query terms=%s top=%s",
                terms[:3],
                [(h.entry.source_text, round(h.score, 3), round(h.keyword_score, 2)) for h in hits[:3]],
            )
        return hits
```

Keyword leg (projection scan — no embedding column loaded):

```python
    _KEYWORD_PROJECTION = [
        "id", "glossary_id", "source_text", "target_text", "source_lang",
        "target_lang", "domain", "register", "pos", "case_sensitive", "notes",
        "source_uri", "source_format", "usage_count", "created_at", "updated_at",
        "glossary_name", "glossary_enabled", "glossary_priority", "glossary_group",
        "glossary_source_uri", "glossary_encoding", "entry_hash",
    ]

    def _keyword_scores(self, query: LexiconQuery) -> dict[str, float]:
        from .store import normalize_term

        terms = [normalize_term(t) for t in candidate_terms(query.source_chunk)]
        terms.append(normalize_term(query.source_chunk[:80]))
        if not any(terms):
            return {}
        try:
            search = self._table.search()
            where = self._build_where(query)
            if where:
                search = search.where(where, prefilter=True)
            cols = [c for c in self._KEYWORD_PROJECTION if c in self._table.schema.names]  # type: ignore[union-attr]
            tbl = search.to_arrow()
            tbl = tbl.select(cols) if hasattr(tbl, "select") else tbl
            rows = tbl.to_pylist()
        except Exception as exc:
            logger.debug("keyword leg scan failed: %s", exc)
            return {}
        scores: dict[str, float] = {}
        for row in rows:
            source_norm = normalize_term(str(row.get("source_text", "")))
            best = 0.0
            for term in terms:
                if not term:
                    continue
                if source_norm == term:
                    best = max(best, 1.0)
                elif source_norm.startswith(term) or term.startswith(source_norm):
                    best = max(best, 0.8)
                elif term in source_norm:
                    best = max(best, 0.6)
            if best:
                scores[str(row.get("id"))] = best
        return scores

    def _rrf_fuse(
        self,
        vector_scores: dict[str, float],
        keyword_scores: dict[str, float],
        depth: int,
    ) -> list[tuple[str, float]]:
        vector_weight = self._vector_weight()
        keyword_weight = self._keyword_weight()
        fused: dict[str, float] = {}
        for rank, (gid, _score) in enumerate(
            sorted(vector_scores.items(), key=lambda kv: -kv[1])[:depth]
        ):
            fused[gid] = fused.get(gid, 0.0) + vector_weight / (self.RRF_K + rank + 1)
        for rank, (gid, _score) in enumerate(
            sorted(keyword_scores.items(), key=lambda kv: -kv[1])[:depth]
        ):
            fused[gid] = fused.get(gid, 0.0) + keyword_weight / (self.RRF_K + rank + 1)
        return sorted(fused.items(), key=lambda kv: -kv[1])

    def _rows_by_id(self, ids: set[str]) -> dict[str, dict[str, Any]]:
        if not ids:
            return {}
        cols = [c for c in self._KEYWORD_PROJECTION if c in self._table.schema.names]  # type: ignore[union-attr]
        rows: dict[str, dict[str, Any]] = {}
        escaped = ", ".join(f"'{_sql_escape(g)}'" for g in ids)
        try:
            tbl = self._table.search().where(f"id IN ({escaped})").to_arrow()
            rows = {str(r["id"]): r for r in tbl.select(cols).to_pylist()}
        except Exception:
            tbl = self._table.to_arrow()
            for r in tbl.select([c for c in cols if c in tbl.column_names]).to_pylist():
                if str(r.get("id")) in ids:
                    rows[str(r["id"])] = r
        return rows
```

Also add module helper `_env_float(name, default)` and `_env_weight` reading envs. `_hybrid_via_arrow` fallback: keep pure-vector (degraded path) but clamp scores with `max(0.0, min(1.0, s))` and construct `LexiconHit(..., keyword_score=0.0)`.

`fingerprint()` implementation on the store:

```python
    _fingerprint_cache: str | None = None

    def fingerprint(self) -> str:
        if self._fingerprint_cache is not None:
            return self._fingerprint_cache
        try:
            metas = self.list_glossaries()
        except Exception:
            return "unavailable"
        payload = "|".join(
            f"{m.id}:{m.name}:{m.entry_count}:{int(m.enabled)}" for m in sorted(metas, key=lambda m: m.id)
        )
        self._fingerprint_cache = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
        return self._fingerprint_cache
```

Invalidate (`self._fingerprint_cache = None`) in `save_glossary`, `delete_glossary`, `toggle_glossary`, and the update paths of `reorder_glossaries`.

- [ ] **Step 4: Run** `uv run pytest tests/core/lexicon -q` — PASS (fix `toggle`-related pins if the add-before-delete fallback path interacts with fingerprint).
- [ ] **Step 5: Commit** `-m "lexicon: real hybrid search — keyword leg + RRF fusion, prefilter, candidate-term vector queries, fingerprint"`

### Task B5: HNSW index creation

**Files:**
- Modify: `src/omniscribe/core/lexicon/lancedb_store.py`
- Test: `tests/core/lexicon/test_lexicon_store.py` (extend)

- [ ] **Step 1: Failing test**

```python
def test_ensure_index_called_after_bulk_save(tmp_path, monkeypatch):
    """save_glossary must drive index creation (idempotent, try-guarded)."""
    calls: list[dict] = []
    store = _store(tmp_path)
    real_create = store._table.create_index
    def spy(**kwargs):
        calls.append(kwargs)
        return real_create(**kwargs)
    monkeypatch.setattr(store._table, "create_index", spy, raising=False)
    store.save_glossary(name="g", format="csv", entries=[{"source": f"s{i}", "target": f"t{i}"} for i in range(200)])
    assert calls, "create_index was never called"
```

- [ ] **Step 2: Run** — FAIL. **Step 3: Implement** on the store:

```python
    INDEX_MIN_ROWS = 128

    def _ensure_index(self) -> None:
        try:
            if self._table.count_rows() < self.INDEX_MIN_ROWS:
                return
            kwargs: dict[str, object] = {
                "metric": VECTOR_INDEX_SPEC["metric"],
                "vector_column_name": "embedding",
                "index_type": "hnsw" if VECTOR_INDEX_SPEC["index_type"] == "hnsw" else "ivf_pq",
                "replace": True,
            }
            if kwargs["index_type"] == "ivf_pq":
                kwargs["num_partitions"] = VECTOR_INDEX_SPEC["num_partitions"]
                kwargs["num_sub_vectors"] = VECTOR_INDEX_SPEC["num_sub_vectors"]
            self._table.create_index(**kwargs)  # type: ignore[arg-type]
        except Exception as exc:
            logger.debug("create_index skipped: %s", exc)
```

Call `self._ensure_index()` at the end of `_ensure_open` (table-exists branch) and after `self._table.add(rows)` in `save_glossary`. Update the module docstring's "built lazily on first query" claim to "created at open and after bulk imports (>=128 rows)". `health()` gains `"index_status"` from a try-guarded `self._table.list_indices()` summary.

- [ ] **Step 4: Run** — PASS. **Step 5: Commit** `-m "lexicon: HNSW index creation at open + after bulk imports (VECTOR_INDEX_SPEC finally used)"`

### Task B6: Migration idempotency + upsert-with-embedding-reuse in `save_glossary`

**Files:**
- Modify: `src/omniscribe/core/lexicon/lancedb_store.py` (`save_glossary`), `src/omniscribe/core/lexicon/store.py` (Protocol signature), `src/omniscribe/core/lexicon/migration.py`, `src/omniscribe/plugins/glossary/service.py` (upsert=True)
- Test: `tests/core/lexicon/test_lexicon_store.py` (extend), `tests/core/lexicon/test_lexicon_migration.py` (extend)

- [ ] **Step 1: Failing tests**

```python
def test_save_glossary_upsert_replaces_same_name_and_uri(tmp_path):
    store = _store(tmp_path)
    first = store.save_glossary(
        name="eu", format="csv", source_uri="file://a.csv",
        entries=[{"source": "EU", "target": "UE"}],
    )
    second = store.save_glossary(
        name="eu", format="csv", source_uri="file://a.csv", upsert=True,
        entries=[{"source": "EU", "target": "Union européenne"}],
    )
    assert second.id == first.id
    metas = store.list_glossaries()
    assert len(metas) == 1
    entries = store.list_entries(first.id)
    assert [e.target_text for e in entries] == ["Union européenne"]


def test_save_glossary_explicit_glossary_id_keeps_id(tmp_path):
    store = _store(tmp_path)
    meta = store.save_glossary(
        name="g", format="csv", glossary_id="legacy-123",
        entries=[{"source": "a", "target": "b"}],
    )
    assert meta.id == "legacy-123"


def test_reimport_reuses_embeddings_for_unchanged_entries(tmp_path):
    store = _store(tmp_path)
    store.save_glossary(name="g", format="csv", entries=[{"source": "a", "target": "b"}])
    calls = {"n": 0}
    model = store._embedding
    real_batch = model.embed_batch
    def counting_batch(texts):
        calls["n"] += len(texts)
        return real_batch(texts)
    object.__setattr__(store, "_embedding", SimpleNamespace(**{**vars(model), "embed_batch": counting_batch})) if False else None
    # (implementation detail: wrap the store's model with a counting proxy —
    # final test code swaps the store's private model attribute for a
    # counting wrapper around the same fake.)
    store.save_glossary(name="g", format="csv", upsert=True,
                        entries=[{"source": "a", "target": "b"}, {"source": "c", "target": "d"}])
    assert calls["n"] == 1  # only the new entry was embedded
```

Migration test:

```python
def test_migration_rerun_does_not_duplicate_glossaries(tmp_path, legacy_factory):
    """Second migration run with dirty legacy state must reuse glossary ids."""
    # Arrange legacy library.json with one glossary id "g-1"; run migration twice
    # (simulating a mid-run crash + retry by re-running against the same
    # artifact dir); assert list_glossaries() has exactly one glossary with
    # id "g-1".
    ...
```

(Reuse the existing `test_lexicon_migration.py` fixtures for legacy state.)

- [ ] **Step 2: Run** — FAIL. **Step 3: Implement:**

`store.py` Protocol `save_glossary` signature: add `glossary_id: str | None = None` and `upsert: bool = False` keyword params (after `priority`).

`lancedb_store.py save_glossary`:

```python
    def save_glossary(
        self,
        *,
        name: str,
        format: str,
        entries: Iterable[dict[str, object]],
        source_uri: str | None = None,
        encoding: str | None = None,
        group: str = "default",
        priority: int = 0,
        glossary_id: str | None = None,
        upsert: bool = False,
    ) -> GlossaryMeta:
```

ID resolution after validation/normalization:

```python
        resolved_id = str(glossary_id).strip() if glossary_id else ""
        if resolved_id:
            # Explicit id (migration): replace same-id rows if present.
            self._table.delete(where=f"glossary_id = '{_sql_escape(resolved_id)}'")
        elif upsert:
            existing = self._find_by_name_and_uri(clean_name, str(source_uri) if source_uri else None)
            if existing is not None:
                resolved_id = existing.id
                self._table.delete(where=f"glossary_id = '{_sql_escape(existing.id)}'")
        glossary_id_final = resolved_id or _new_id()
```

Embedding reuse (replaces the unconditional embed):

```python
        reusable = self._embeddings_by_entry_hash(glossary_id_final)
        source_texts = [str(e["source"]) for e in normalized]
        hashes = [entry_hash(str(e["source"]), str(e["target"])) for e in normalized]
        missing_idx = [i for i, h in enumerate(hashes) if h not in reusable]
        fresh = self._embedding.embed_batch([source_texts[i] for i in missing_idx])
        embeddings: list[list[float]] = [list(reusable[h]) for h in hashes]
        for slot, i in enumerate(missing_idx):
            embeddings[i] = fresh[slot]
```

(`_embeddings_by_entry_hash(gid)` projects `entry_hash, embedding` for rows of that glossary id, returns `dict[str, list[float]]`, try-guarded to `{}` when columns/rows missing.) Rows carry `glossary_id=glossary_id_final`; `GlossaryMeta` returns that id. Invalidate fingerprint. `_find_by_name_and_uri(name, uri)` scans `list_glossaries()` for a case-insensitive name match and matching (or both-None) `source_uri`.

`migration.py`: `_existing_glossary_meta` simplifies to `return store.get_glossary(glossary_id)`; both save branches pass `glossary_id=str(g["id"])` (idempotent re-run: explicit id deletes prior rows then re-saves under the same id — no duplicates). Fix the misleading comment.

`plugins/glossary/service.py` (~line 310 and ~367): add `upsert=True` to both `save_glossary(...)` calls (re-import replaces the same name+source; the import API semantics get a note in the route docstring).

- [ ] **Step 4: Run** lexicon + glossary-plugin tests — PASS. **Step 5: Commit** `-m "lexicon: idempotent migration ids, upsert-on-reimport with embedding reuse"`

### Task B7: Normalization unification + exact_lookup case sensitivity + min_score default in the RAG node

**Files:**
- Modify: `src/omniscribe/core/lexicon/helpers.py`, `src/omniscribe/core/lexicon/lancedb_store.py` (`exact_lookup`), `src/omniscribe/core/translate/nodes.py` (already passes `lexicon_min_score` from A5 — verify)
- Test: `tests/core/lexicon/test_lexicon_store.py`, `tests/core/lexicon/test_toggle_glossary_atomic.py` (if helpers pinned)

- [ ] **Step 1: Failing tests**

```python
def test_merge_and_preview_agree_on_casefold(tmp_path):
    """ß vs ss must be the same key in BOTH merge and preview."""
    store = _store(tmp_path)
    store.save_glossary(name="g1", format="csv", entries=[{"source": "Straße", "target": "rue"}])
    store.save_glossary(name="g2", format="csv", entries=[{"source": "STRASSE", "target": "boulevard"}])
    merged = merged_enabled_glossary(store)
    assert len(merged.entries) == 1  # same casefold key
    report = preview(store)
    assert report["count"] == 1
    assert report["conflicts"]  # two glossaries, two targets → conflict


def test_exact_lookup_respects_case_sensitive_flag(tmp_path):
    store = _store(tmp_path)
    store.save_glossary(name="g", format="csv", entries=[
        {"source": "EU", "target": "UE", "case_sensitive": True},
        {"source": "Nato", "target": "OTAN", "case_sensitive": False},
    ])
    assert [e.source_text for e in store.exact_lookup("eu", source_lang="", target_lang="")] == ["Nato"]
    assert [e.source_text for e in store.exact_lookup("EU", source_lang="", target_lang="")] == ["EU", "Nato"]
```

- [ ] **Step 2: Run** — FAIL. **Step 3: Implement:**

`helpers.py`: both `merged_enabled_glossary` and `preview` key on `normalize_term(entry.source_text)` (import from `.store`).

`exact_lookup`: strip; compare per-row honoring `case_sensitive`:

```python
        probe = source_text.strip()
        if not probe:
            return []
        probe_norm = normalize_term(probe)
        ...
        for row in tbl.to_pylist():
            row_source = str(row.get("source_text", "")).strip()
            if bool(row.get("case_sensitive", False)):
                if row_source != probe:
                    continue
            elif normalize_term(row_source) != probe_norm:
                continue
```

(lang filters unchanged). Existing `test_exact_lookup_case_insensitive` pin keeps passing (case-insensitive rows still match).

- [ ] **Step 4: Run** — PASS. **Step 5: Commit** `-m "lexicon: unified normalize_term everywhere, case_sensitive exact lookup"`

### Task B8: Retrieval recall fixture + full-suite gate

**Files:**
- Test: `tests/core/lexicon/test_recall_fixture.py` (new)

- [ ] **Step 1: Write the fixture test** — deterministic `MappedEmbedder` (dict term→orthogonal unit vector; unknown terms → zero vector) so recall is exact:

```python
def test_recall_at_3_hybrid(tmp_path):
    """Known-relevant entries must appear in top-3 for hybrid queries."""
    store = LanceDBLexiconStore(path=tmp_path, embedding_model=MappedEmbedder({
        "privacy": _unit(0), "règlement": _unit(1), "données": _unit(2),
        "GDPR": _unit(3),
    }))
    store.save_glossary(name="terms", format="csv", entries=[
        {"source": "privacy", "target": "confidentialité"},
        {"source": "règlement", "target": "règlement intérieur"},
        {"source": "GDPR", "target": "RGPD"},
        {"source": "unrelated", "target": "sans rapport"},
    ])
    hits = store.hybrid_query(LexiconQuery(source_chunk="GDPR privacy règlement", limit=3))
    top_sources = {h.entry.source_text for h in hits[:3]}
    assert {"GDPR", "privacy"} <= top_sources
```

- [ ] **Step 2: Run full fast-tier suite** `uv run pytest tests/core/translate tests/core/lexicon tests/plugins -q` — all green.
- [ ] **Step 3: mypy** on touched paths: `uv run --with orjson mypy src/omniscribe/core/translate src/omniscribe/core/lexicon src/omniscribe/plugins/translate src/omniscribe/plugins/glossary` — clean.
- [ ] **Step 4: Commit** `-m "lexicon: deterministic recall@3 fixture test"`

---

## Self-review notes

- Spec items 1–17 map to tasks: 1→A5/A7, 2→A3+A5+B1(to_prompt_block/save sanitize in A3 & B1? — sanitize at glossary import lands in A3 via builder + `Glossary.to_prompt_block` sanitize added in A3 step (add `sanitize_prompt_input` to `to_prompt_block` lines), 3→A1/A4/A5/A7, 4→A2/A6, 5→A1/A5, 6→A3/A6, 7→A7/A8, 8→A8, 9→B4, 10→B3/B4, 11→B5, 12→B2, 13→A1(min_score field)+B4, 14→B6, 15→B6, 16→B7, 17→B4 (debug log)+B8.
- Wait — sanitize `Glossary.to_prompt_block` is not explicitly in a task's code above; **it is part of Task A3**: extend `glossary.py::to_prompt_block` to build lines from `sanitize_prompt_input(e.source)`/`sanitize_prompt_input(e.target)` with a failing test added to `test_glossary.py` (`test_prompt_block_sanitizes_entries`). Same task's Step 5 commit.
- `LexiconHit` field order change is backward-compatible via the default; `LexiconStore` Protocol gains `fingerprint` — fakes in tests that assert `isinstance(store, LexiconStore)` need the method (add to shared fake).
