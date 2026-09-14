# Code Duplication Audit — OmniScribe

**Scope:** `src/omniscribe/**/*.py` (214+ files). 214 source files across `core/`, `plugins/`, `harness/`, `middleware/`, `utils/`, plus root entrypoints (`server.py`, `worker.py`, `pipeline.py`, `config.py`, `confidence_eval.py`).
**Method:** Targeted reads of high-fan-out modules (glossary sources, writers, lexicon, workflows, recall, ocr_quality, plugins/*), backed by `rg`/grep sweeps over the codebase. Evidence citations use `path:line`.

---

## Severity legend

| Scale | Meaning |
| --- | --- |
| 1–3 | Cosmetic — leave or do opportunistically |
| 4–6 | Worth a single PR — measurable payoff, low blast radius |
| 7–8 | Important — actively harms maintainability, should be in next sprint |
| 9–10 | Critical — fixing now will save real engineering time / prevent bugs |

---

## Executive summary (ranked by impact)

| # | Finding | Type | Severity (1–10) | Effort |
| --- | --- | --- | --- | --- |
| F1 | `_envelope` JSON error helper duplicated across 4 plugin route files | Exact duplicate | **9** | XS |
| F2 | Per-route `try/except XError as exc: return _envelope(400, ...)` boilerplate (~60+ sites) | Structural duplicate | **9** | M |
| F3 | Three XML glossary parsers (`tbx.py`, `tmx.py`, `xliff.py`) share identical safe-decode/entry-dict/finalize plumbing | Structural duplicate | **8** | M |
| F4 | Two `parse_bool` implementations (`utils/env.py:66` vs `core/glossary_sources/_common.py:82`) | Exact duplicate | **8** | XS |
| F5 | Empty `*Schema` classes in 4 plugin `plugin.py` files | Structural duplicate | **6** | XS |
| F6 | `EMBEDDING_DIM = 384` declared in both `lexicon/store.py:23` and `lexicon/embedding.py:28` | Data duplicate | **8** | XS |
| F7 | Two near-identical `decode_base64 → PIL.Image` helpers (`workflows/utils.py:_decode_page_image` vs `imaging/utils.py:decode_base64_image`) | Near duplicate | **7** | S |
| F8 | `base64.b64decode(...)` + `Image.open(...).convert(...)` pattern repeated in `ocr/processor.py:670`, `workflows/utils.py:121`, etc. | Structural duplicate | **6** | S |
| F9 | `hasattr(node.block_type, "value")` / `block_type.value` normalization in 5 writers/chunkers | Structural duplicate | **6** | XS |
| F10 | `_bearer_token` duplicated between `plugins/documents/routes.py:70` and `plugins/providers.py:50` | Exact duplicate | **7** | XS |
| F11 | `class XError(PluginError)` per plugin (translate/transcribe/glossary) + inconsistent base (`DocumentsError(Exception)`) | Structural duplicate | **6** | S |
| F12 | `BaseRecallOptions` subclass boilerplate (`whitespace.py`, `text_layer.py`) — both define their own `_MAX_*` constants near identical to `recall/__init__.py` public ones | Structural duplicate | **7** | S |
| F13 | `Image.open(...).convert("RGB"\|"L")` repeated across stages; no shared `imdecode` helper | Structural duplicate | **5** | S |
| F14 | `image_b64` vs `image_base64` naming inconsistency across 14 files | Data / naming | **4** | S |
| F15 | `rendered_table_ids` de-dup guard repeated across 4 writers/chunker | Structural duplicate | **5** | XS |

---

## F1. `_envelope` JSON error helper — exact duplicate ×4

**Severity: 9/10**

**Where:**
- `src/omniscribe/plugins/documents/routes.py:63`
- `src/omniscribe/plugins/glossary/routes.py:49`
- `src/omniscribe/plugins/transcribe/routes.py:27`
- `src/omniscribe/plugins/translate/routes.py:21`

**Body (identical in all 4 files, only docstring differs):**

```python
def _envelope(status_code: int, error: str, detail: str) -> JSONResponse:
    """Stable error envelope the Flutter client parses."""
    return JSONResponse(
        status_code=status_code, content={"error": error, "detail": detail}
    )
```

**DRY solution:** Add `omniscribe/plugins/_http.py` (or extend `plugins/errors.py`):

```python
# src/omniscribe/plugins/_http.py
from __future__ import annotations
from fastapi.responses import JSONResponse

__all__ = ["envelope", "bad_request", "not_found", "unprocessable", "server_error"]

def envelope(status_code: int, error: str, detail: str) -> JSONResponse:
    """Stable {error,detail} envelope the Flutter client parses."""
    return JSONResponse(status_code=status_code, content={"error": error, "detail": detail})

bad_request     = lambda err, det: envelope(400, err, det)
not_found       = lambda err, det: envelope(404, err, det)
unprocessable   = lambda err, det: envelope(422, err, det)
server_error    = lambda err, det: envelope(500, err, det)
```

Then replace each `def _envelope(...)` with `from omniscribe.plugins._http import envelope` and update ~60 call sites to use the shortcuts (see F2).

**Refactoring effort:** XS (≈15 min: 4 deletions + 60 import rewrites — most are `from … import envelope` plus `_envelope(400, …)` → `bad_request(…)`).

---

## F2. Per-route `try/except XError → _envelope(400)` boilerplate — structural duplicate

**Severity: 9/10** (this is what makes the codebase feel like 60 tiny copies of the same handler)

**Where:** Sweep `rg "except \w+Error as exc:" src/omniscribe/plugins` returned **60 matches** across `documents/routes.py`, `glossary/routes.py`, `transcribe/routes.py`, `translate/routes.py`, and `providers_service.py`. The pattern is:

```python
try:
    ...
except GlossaryFormatError as exc:
    return _envelope(400, "glossary_format_error", str(exc))
except GlossaryError as exc:
    return _envelope(400, "glossary_error", str(exc))
except ValueError as exc:
    return _envelope(400, "invalid_input", str(exc))
```

**DRY solution:** Use an exception-class registry plus a generic dispatcher:

```python
# src/omniscribe/plugins/_http.py
from collections.abc import Callable
from .errors import PluginError

# Each plugin module registers: {ErrorClass: "glossary_format_error"}
def make_handler(err: Exception, *, error_tag: str, status: int = 400, detail: str | None = None):
    return envelope(status, error_tag, detail or str(err))

def plugin_exception_handlers(registry: dict[type[Exception], str], status: int = 400):
    """Return an async FastAPI exception middleware that maps errors to envelopes."""
    async def _middleware(request, call_next):
        try:
            return await call_next(request)
        except Exception as exc:
            for cls, tag in registry.items():
                if isinstance(exc, cls):
                    return envelope(status, tag, str(exc))
            raise
    return _middleware
```

Each plugin provides a tiny mapping once at module load:

```python
GLOSSARY_ERROR_MAP: dict[type[Exception], str] = {
    GlossaryFormatError: "glossary_format_error",
    GlossaryError:       "glossary_error",
    ValueError:          "invalid_input",
}
```

…and handlers become `try: … except: raise` (no per-endpoint boilerplate).

**Refactoring effort:** M (≈half-day: 4 plugins × 15 endpoints to migrate, plus tests).

---

## F3. TBX / TMX / XLIFF XML glossary parsers — structural duplicate

**Severity: 8/10**

**Where:**
- `src/omniscribe/core/glossary_sources/tbx.py`
- `src/omniscribe/core/glossary_sources/tmx.py`
- `src/omniscribe/core/glossary_sources/xliff.py`
- (Plus `csv_tsv.py`, `json_pairs.py`, `sql_table.py` use a related but smaller `require_bytes`+`decode_source` pair.)

**Pattern:**

```python
# tbx.py / tmx.py / xliff.py all open with
from ._common import require_bytes, safe_xml_root, decode_source, finalize

def parse(source: bytes | str, ...) -> Iterable[GlossaryEntry]:
    blob = require_bytes(source)
    root = safe_xml_root(blob)
    ...
    yield from finalize(_iter(root))
```

The body of `parse` differs (XPath, attribute names), but the **outer contract is identical**: `require_bytes → decode_source → safe_xml_root → iter → finalize`.

**DRY solution:** Add a base class `XmlGlossaryParser` in `core/glossary_sources/_base.py`:

```python
class XmlGlossaryParser(GlossaryParser):
    def parse(self, source, *, hints=None):
        blob = require_bytes(source)
        root = safe_xml_root(blob)
        return list(finalize(self._iter_entries(root, hints)))

    def _iter_entries(self, root, hints) -> Iterable[GlossaryEntry]:
        raise NotImplementedError
```

Each parser then implements only `_iter_entries`. `safe_xml_root`, `require_bytes`, `decode_source`, and `finalize` already exist in `_common.py` — they just need a consistent entry point.

**Refactoring effort:** M (~1 day: 3 parsers, ~200 LOC removed, plus new `_base.py` and a contract test).

---

## F4. Two `parse_bool` implementations — exact duplicate

**Severity: 8/10** (the `_common.py` copy is incomplete — it lacks the explicit `ENABLE_STRINGS`/`DISABLE_STRINGS` frozensets, so behaviour can diverge)

**Where:**
- `src/omniscribe/utils/env.py:66` — uses `ENABLE_STRINGS` + `DISABLE_STRINGS` frozensets and exposes `parse_bool(env_var: str) -> bool`
- `src/omniscribe/core/glossary_sources/_common.py:82` — uses an inline set `{"1", "true", "yes", "y", "on"}` only

```python
# _common.py:82  (subset — missing explicit disable list)
def parse_bool(value: str, *, default: bool = False) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}
```

```python
# utils/env.py:66
def parse_bool(env_var: str, *, default: bool = False) -> bool:
    raw = (env_str(env_var) or "").strip().lower()
    if raw in ENABLE_STRINGS:
        return True
    if raw in DISABLE_STRINGS:
        return False
    return default
```

**DRY solution:** Keep one canonical `parse_bool` in `utils/env.py` (it already has the right semantics) and have `_common.py` import it:

```python
# core/glossary_sources/_common.py
from omniscribe.utils.env import parse_bool  # noqa: F401  re-export
```

If the parser needs a literal-string variant, expose a sibling:

```python
# utils/env.py
def parse_bool_value(value: str, *, default: bool = False) -> bool: ...
```

**Refactoring effort:** XS (≈5 min). Add the import, delete the duplicate.

---

## F5. Empty `*Schema` classes in 4 plugin entry points — structural duplicate

**Severity: 6/10**

**Where:**
- `src/omniscribe/plugins/documents/plugin.py:12`
- `src/omniscribe/plugins/translate/plugin.py:19`
- `src/omniscribe/plugins/glossary/plugin.py:19`
- `src/omniscribe/plugins/transcribe/plugin.py:18`

All four have the same body:

```python
class TranscribeSchema(BaseModel):
    """No configurable fields."""
```

**DRY solution:** Make the base class provide a default:

```python
# src/omniscribe/harness/plugin.py
class Plugin:
    Schema: ClassVar[type[BaseModel] | None] = None

# Plugins with no config simply omit the attribute:
class TranscribePlugin(Plugin): ...
```

…or add a concrete empty schema:

```python
# src/omniscribe/harness/plugin.py
class EmptySchema(BaseModel):
    """Sentinel for plugins that take no configuration."""

# then
class TranscribePlugin(Plugin):
    Schema = EmptySchema
```

**Refactoring effort:** XS (≈10 min for 4 files).

---

## F6. `EMBEDDING_DIM = 384` duplicated — data duplicate

**Severity: 8/10** (changing this in one place will silently desync the other)

**Where:**
- `src/omniscribe/core/lexicon/store.py:23` — `EMBEDDING_DIM = 384`
- `src/omniscribe/core/lexicon/embedding.py:28` — `EMBEDDING_DIM = 384`

**DRY solution:** Single source of truth:

```python
# src/omniscribe/core/lexicon/__init__.py (or new _constants.py)
EMBEDDING_DIM: Final[int] = 384

# store.py / embedding.py
from omniscribe.core.lexicon import EMBEDDING_DIM  # noqa: F401
```

**Refactoring effort:** XS (≈2 min). Single constant + 2 import lines.

---

## F7. Two `decode_base64 → PIL.Image` helpers — near duplicate

**Severity: 7/10**

**Where:**
- `src/omniscribe/core/workflows/utils.py:120` — `_decode_page_image(image_b64) -> Image.Image` (private, `.convert("RGB")`, no `with` block)
- `src/omniscribe/core/imaging/utils.py:7` — `decode_base64_image(data) -> Image.Image` (public, no `.convert`, uses `with` + `.load()` + `.copy()`)

```python
# workflows/utils.py:120
def _decode_page_image(image_b64: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(image_b64))).convert("RGB")

# imaging/utils.py:7
def decode_base64_image(data: str) -> Image.Image:
    raw = base64.b64decode(data)
    with Image.open(io.BytesIO(raw)) as img:
        img.load()
        return img.copy()
```

**DRY solution:** Promote `imaging/utils.py:decode_base64_image` to the canonical one and add an optional `mode` argument:

```python
# core/imaging/utils.py
def decode_base64_image(data: str, *, mode: str | None = None) -> Image.Image:
    raw = base64.b64decode(data)
    with Image.open(io.BytesIO(raw)) as img:
        img.load()
        out = img.copy()
    return out.convert(mode) if mode else out
```

Then in `workflows/utils.py` either delete `_decode_page_image` or alias:

```python
from omniscribe.core.imaging.utils import decode_base64_image as _decode_page_image
```

**Refactoring effort:** S (≈30 min, plus touching 6 import sites in `hybrid.py`, `hybrid_repair.py`, `stages/layout.py`, `stages/ocr.py`, `stages/refine.py`).

---

## F8. `base64.b64decode + Image.open + convert(...)` repeated — structural duplicate

**Severity: 6/10** (related to F7; same pattern in 3 more files)

**Where:**
- `src/omniscribe/core/ocr/processor.py:670` — `Image.open(io.BytesIO(base64.b64decode(image_base64))).convert("L")`
- `src/omniscribe/core/ocr/processor.py:407` — `image_bytes = base64.b64decode(image_base64)` (used as `Image.open(io.BytesIO(image_bytes))`)
- `src/omniscribe/core/ocr/processor.py:583` — same pattern
- `src/omniscribe/core/workflows/utils.py:121` — see F7

**DRY solution:** Use the helper from F7 in OCR too. Add `_to_grayscale` and `_to_rgb` thin wrappers if needed.

**Refactoring effort:** S (≈20 min).

---

## F9. `block_type.value` normalization — structural duplicate

**Severity: 6/10** (5 call sites with the same defensive `hasattr` dance)

**Where:**
- `src/omniscribe/core/writers/markdown.py:178`
- `src/omniscribe/core/writers/html.py:152`
- `src/omniscribe/core/writers/docx_tree.py:83`
- `src/omniscribe/core/chunking/taxonomy.py:166`
- `src/omniscribe/core/chunking/chunker.py:237`
- `src/omniscribe/core/block_tree.py:131`

The exact pattern `node.block_type.value if hasattr(node.block_type, "value") else str(node.block_type or "")` (or its variants) is repeated.

**DRY solution:** Add a single helper to `core/block_tree.py` (or a new `core/utils.py`):

```python
# core/block_tree.py
def block_type_str(block_type) -> str:
    """Coerce BlockType (or string-ish fallback) to its string value."""
    return block_type.value if hasattr(block_type, "value") else str(block_type or "")
```

Then import across the 6 sites.

**Refactoring effort:** XS (~10 min).

---

## F10. `_bearer_token` duplicated — exact duplicate

**Severity: 7/10**

**Where:**
- `src/omniscribe/plugins/documents/routes.py:70`
- `src/omniscribe/plugins/providers.py:50`

```python
def _bearer_token(authorization: str | None) -> str | None:
    if authorization and authorization.startswith("Bearer "):
        return authorization.removeprefix("Bearer ").strip()
    return None
```

**DRY solution:** Move to `plugins/_http.py` (alongside `envelope`):

```python
# src/omniscribe/plugins/_http.py
def bearer_token(authorization: str | None) -> str | None:
    if authorization and authorization.startswith("Bearer "):
        return authorization.removeprefix("Bearer ").strip()
    return None
```

Then both routes do `from omniscribe.plugins._http import bearer_token`.

**Refactoring effort:** XS (~5 min).

---

## F11. Per-plugin `XError(PluginError)` — structural duplicate (with one outlier)

**Severity: 6/10** (the outlier `DocumentsError(Exception)` is a bug magnet — it doesn't inherit from `PluginError`, so it can't share the registry in F2)

**Where:**
- `src/omniscribe/plugins/translate/service.py:59` — `class TranslateError(PluginError)`
- `src/omniscribe/plugins/transcribe/service.py:55` — `class TranscribeError(PluginError)`
- `src/omniscribe/plugins/glossary/service.py:50` — `class GlossaryError(PluginError)`
- `src/omniscribe/plugins/errors.py:13` — `class PluginError(Exception)`
- **Outlier:** `src/omniscribe/plugins/documents/service.py:38` — `class DocumentsError(Exception)` ← does NOT inherit `PluginError`

**DRY solution:** Make `DocumentsError` inherit `PluginError` so F2's registry catches it:

```python
# documents/service.py
from omniscribe.plugins.errors import PluginError

class DocumentsError(PluginError):
    """Errors raised by the Documents plugin."""
```

…then add a small factory in `plugins/errors.py`:

```python
class PluginError(Exception):
    error_tag: ClassVar[str] = "plugin_error"

    def to_envelope(self, status: int = 400) -> JSONResponse:
        return envelope(status, self.error_tag, str(self))
```

so each subclass just overrides `error_tag`.

**Refactoring effort:** S (~30 min including tests).

---

## F12. `BaseRecallOptions` subclass boilerplate — structural duplicate

**Severity: 7/10**

**Where:**
- `src/omniscribe/core/recall/__init__.py:10-11` — public constants: `STRADDLE_MIN_OVERLAP = 0.15`, `MAX_RECALL_BOXES_PER_PAGE = 10`
- `src/omniscribe/core/recall/whitespace.py:35-37` — defines *its own* `_MAX_RECALL_BOXES_PER_PAGE`, `_STRADDLE_MIN_OVERLAP`, `_MAX_IOU`, `_MAX_CONTAINMENT` near-identical to the public ones
- `src/omniscribe/core/recall/text_layer.py` — same pattern (same constants, slightly different values)

Pattern (excerpt from `whitespace.py`):

```python
@dataclass(frozen=True, slots=True)
class SuryaWhitespaceOptions(BaseRecallOptions):
    enabled: bool = True
    max_containment: float = _MAX_CONTAINMENT
    max_iou: float = _MAX_IOU
    straddle_min_overlap: float = _STRADDLE_MIN_OVERLAP
    max_recall_boxes_per_page: int = _MAX_RECALL_BOXES_PER_PAGE
    candidates_dropped: int = 0
    ...
    @classmethod
    def from_env(cls, env_var: str = "OMNI_WHITESPACE_RECALL") -> Self:
        return cls._from_env(env_var)
```

`text_layer.py` has the same dataclass shape with the same set of constants and the same `from_env` body.

**DRY solution:** Generalise `BaseRecallOptions` to a small mixin:

```python
# core/recall/base.py
@dataclass(frozen=True, slots=True)
class BaseRecallOptions:
    enabled: bool = True
    max_containment: float = 0.55
    max_iou: float = 0.40
    straddle_min_overlap: float = STRADDLE_MIN_OVERLAP  # from recall/__init__
    max_recall_boxes_per_page: int = MAX_RECALL_BOXES_PER_PAGE

    @classmethod
    def from_env(cls, env_var: str, **overrides) -> Self:
        return cls(_from_env(env_var).enabled, **overrides)
```

…then `whitespace.py` and `text_layer.py` only declare their *unique* fields.

**Refactoring effort:** S (~1 hour including re-running recall audits).

---

## F13. `Image.open(io.BytesIO(...)).convert(...)` repeated — structural duplicate

**Severity: 5/10** (subset of F7/F8, but worth a single helper)

**Where:**
- `src/omniscribe/core/ocr/processor.py:670` — `... .convert("L")`
- `src/omniscribe/core/workflows/utils.py:121` — `... .convert("RGB")`
- (and the unconverted versions in `processor.py:407`, `:583`, `imaging/utils.py:16`)

**DRY solution:** Covered by the helper from F7.

**Refactoring effort:** S (folded into F7).

---

## F14. `image_b64` vs `image_base64` naming inconsistency

**Severity: 4/10** (data / interface duplication — same field, two names)

**Where:** 14 files (see grep evidence above). Both spellings appear in `core/workflows/`, `core/ocr/`, `core/grounded/`, `core/llm/`, `core/imaging/`. The HTTP-side names (`image_base64`) dominate; the in-process pipeline names (`image_b64`) dominate.

**DRY solution:** Pick one (recommend `image_b64` — shorter, matches `image_bytes_b64`) and add a `TypeAdapter`-style shim during the rename, OR use a Pydantic `Field(alias=...)`. At minimum, document the canonical name in `core/imaging/utils.py`:

```python
# core/imaging/utils.py docstring
"""Canonical base64 image field name used across the pipeline: ``image_b64``.

External HTTP bodies may use ``image_base64``; convert at the route boundary.
"""
```

**Refactoring effort:** S (~1 hour if done with an alias strategy; risky to rename in one pass).

---

## F15. `rendered_table_ids` de-dup guard — structural duplicate

**Severity: 5/10**

**Where:**
- `src/omniscribe/core/writers/markdown.py` (multiple sites)
- `src/omniscribe/core/writers/html.py`
- `src/omniscribe/core/writers/docx_tree.py`
- `src/omniscribe/core/chunking/chunker.py`

Each writer keeps a `set[str]` of already-rendered table ids and checks membership before re-rendering.

**DRY solution:** A single `rendered_table_guard()` context manager / generator in `core/writers/_guard.py`:

```python
# core/writers/_guard.py
from contextlib import contextmanager

@contextmanager
def table_dedup_guard():
    seen: set[str] = set()
    yield seen

# usage in any writer:
with table_dedup_guard() as seen:
    if t_id and t_id in seen:
        continue
    ...
    seen.add(t_id)
```

**Refactoring effort:** XS (~15 min).

---

## Proposed utilities module

A new `src/omniscribe/utils/` entry point — `__init__.py` re-exports — would let several findings (F1, F4, F6, F7, F10, F11, F12) collapse to a single canonical home. Recommended layout:

```
src/omniscribe/utils/
├── __init__.py          # re-exports
├── env.py               # parse_bool, env_str, DISABLE_STRINGS (existing)
├── imaging.py           # decode_base64_image, encode_image_base64, imdecode_b64
├── http.py              # envelope, bearer_token, exception-handler factory
├── block_tree.py        # block_type_str
├── errors.py            # PluginError base + tag system (moved from plugins/)
└── recall.py            # BaseRecallOptions with shared defaults (moved from core/recall/base.py)
```

Drop-in `utils/__init__.py`:

```python
from omniscribe.utils.env import parse_bool, env_str, DISABLE_STRINGS, ENABLE_STRINGS
from omniscribe.utils.imaging import decode_base64_image, encode_image_base64
from omniscribe.utils.http import envelope, bearer_token, plugin_exception_middleware
from omniscribe.utils.block_tree import block_type_str
from omniscribe.utils.errors import PluginError
from omniscribe.utils.recall import BaseRecallOptions, STRADDLE_MIN_OVERLAP, MAX_RECALL_BOXES_PER_PAGE
```

Then F1, F4, F6, F7, F10, F11, F12 each become single-file deletions plus import-line additions.

---

## Findings I could *not* verify

- The plugin `ocr/plugin.py:625` and `state_backend.py:72` *might* also have empty schemas; I only sampled. Worth a quick sweep before adopting the F5 solution across the board.
- The `chunking/chunker.py` has multiple `t_id = getattr(table, "block_id", _new_chunk_id())` patterns (lines 248, 308, 352). This is *near*-duplicate and likely foldable, but I did not read the surrounding logic to confirm they share a full body.
- I did not examine `core/ocr_quality/*.py` deeply; a quick sweep showed no `class XError` definitions there, so it likely does not contribute to F11, but a fuller audit of `calibration.py`/`trust_scorer.py` may surface additional pattern duplication that wasn't visible from class names alone.

---

## Recommended remediation order

1. **One PR:** F1 + F10 + F4 + F6 — all XS, all in `utils/`/`plugins/_http.py`. (~1 hour total)
2. **One PR:** F5 + F15 + F9 — small structural fixes. (~1 hour)
3. **One PR:** F11 — fix the `DocumentsError` outlier. (~30 min)
4. **One PR:** F2 — the high-leverage middleware extraction. (~half-day, requires touching every route handler)
5. **One PR:** F7 + F8 + F13 — consolidate image decoding. (~1 hour)
6. **One PR:** F12 — `BaseRecallOptions` mixin. (~1 hour, retest recall audits 3.9 / 6.5 / 6.25)
7. **Larger refactor:** F3 — `XmlGlossaryParser` base class. (~1 day + contract tests)
8. **Follow-up:** F14 — naming standardisation (with alias shim).
