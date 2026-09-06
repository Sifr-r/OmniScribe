"""JSON parsers + small helpers for grounded OCR responses.

Three parsers + their shared helpers live here:

- :func:`parse_glm_layout_details` — GLM-OCR / vLLM ``layout_details``
  block list, either flat or nested per page.
- :func:`_parse_grounded_json` — generic JSON-array parser used by
  :mod:`.prompted` (Qwen2.5-VL / Qwen3-VL style responses).

The non-content label set (``_NON_CONTENT_LABELS``) and the
shared ``_clamp`` helper sit here too — single source of truth for
which structural regions get dropped before the pipeline embeds them.
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections.abc import Sequence
from typing import Any

from omniscribe.core.grounded.models import (
    GroundedBlock,
    GroundedResponse,
)
from omniscribe.core.ocr_quality.events import emit as _emit_quality_event

logger = logging.getLogger(__name__)

# Labels we treat as *non-content* — structural regions that aren't meant
# to carry selectable text. Newer grounded responses emit labels like
# "title", "list_item", "form_field", "diagram_node" etc. alongside "text";
# the old handwritten fixture was pure "text" + "image". Instead of allow-
# listing content labels (brittle across schema versions) we deny-list the
# structural ones.
_NON_CONTENT_LABELS = frozenset(
    {
        "empty_line",  # unfilled underline fields
        "signature_line",  # form signature placeholder
        "list_marker",  # lone bullet/dash glyphs
    }
)

# F1.16 audit fix: structural label set used by the GLM
# ``parse_glm_layout_details`` path. The prior implementation used a
# strict-equality allow-list (``b.get("label") != "text"``) which
# silently dropped any new label the upstream schema added. The
# Qwen path uses a schema-driven approach (it does not gate on the
# ``label`` field at all — it reads the ``content`` and bbox keys
# directly), so the two parsers never agreed on what counts as
# content. We use a deny-list of GLM's known structural labels here;
# any new content label (``"text"``, ``"title"``, ``"list_item"``,
# ``"form_field"``, etc.) flows through and the existing tests stay
# green because ``"image"`` and ``"figure"`` are still dropped.
_GLM_STRUCTURAL_LABELS = frozenset(
    {
        "image",  # raster region, no text
        "figure",  # captioned illustration
        "table",  # tabular region, no inline text
        "equation",  # math block
        "chart",  # data viz region
        "diagram",  # flow chart / org chart
        "stamp",  # annotation graphic
        "qrcode",  # machine-readable graphic
    }
)

# Accepted bbox/content key aliases — different VLMs use different field
# names. The first match wins. Keep the canonical (`bbox_2d` / `content`)
# pair at the top so the common path stays the cheapest.
_BBOX_KEYS: tuple[str, ...] = (
    "bbox_2d",
    "bbox",
    "box_2d",
    "box",
    "bounding_box",
    "coordinates",
)
_CONTENT_KEYS: tuple[str, ...] = (
    "content",
    "text",
    "label_text",
    "ocr_text",
)

_JSON_FENCE = re.compile(
    r"```(?:json)?\s*(\[[\s\S]*?\]|\{[\s\S]*?\})\s*```", re.IGNORECASE
)
_BARE_ARRAY = re.compile(r"(\[[\s\S]*\])")


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


def _extract_bbox(item: dict[str, Any]) -> list[Any] | tuple[Any, ...] | None:
    """Pull a 4-element bbox sequence out of an item using known aliases.

    Returns the raw 4-sequence (caller normalizes) or ``None`` if no
    recognized key holds a length-4 list/tuple. The first matching key
    wins, so canonical ``bbox_2d`` is preferred.
    """
    for key in _BBOX_KEYS:
        if key in item:
            value = item[key]
            if isinstance(value, (list, tuple)) and len(value) == 4:
                return value
    return None


def _normalize_bbox(
    bbox: Sequence[float], img_w: int, img_h: int
) -> tuple[float, float, float, float] | None:
    """Convert a raw 4-tuple of bbox coordinates to normalized 0..1 XYXY.

    Tries three shapes in order of confidence:

    1. **Already normalized** — if all four values are in ``[0, 1]`` the
       VLM emitted relative coords; pass them through.
    2. **XYWH** — if ``[x0, y0, w, h]`` interpretation lands inside the
       image AND the literal XYXY interpretation would put ``x1`` past
       the image width, treat the last two values as sizes.
    3. **Pixel XYXY** — divide by image dimensions and clamp.

    Returns ``None`` for invalid input (non-numeric, fully degenerate).
    """
    try:
        x0, y0, x1, y1 = (float(v) for v in bbox)
    except (TypeError, ValueError):
        return None

    if min(x0, y0, x1, y1) < 0:
        return None

    # Already normalized? Skip the divide.
    if max(x0, y0, x1, y1) <= 1.0:
        if x1 > x0 and y1 > y0:
            return _clamp(x0), _clamp(y0), _clamp(x1), _clamp(y1)
        return None

    if img_w <= 0 or img_h <= 0:
        return None

    # Canonical pixel XYXY — the Qwen-VL / Qwen2.5-VL / Qwen3-VL shape.
    # We require both that the read is non-degenerate AND that it lands
    # inside the image; this is the fast path so keep it first.
    if x1 > x0 and y1 > y0 and x1 <= img_w + 1 and y1 <= img_h + 1:
        return (
            _clamp(x0 / img_w),
            _clamp(y0 / img_h),
            _clamp(x1 / img_w),
            _clamp(y1 / img_h),
        )

    # XYWH fallback: only fires when the literal XYXY read is invalid
    # (degenerate, or extends past the image). Interpreting the last
    # two values as (w, h) sometimes rescues InternVL / GLM-OCR style
    # responses that emit ``[x, y, w, h]`` instead of ``[x0, y0, x1, y1]``.
    if x1 > 0 and y1 > 0 and x0 + x1 <= img_w + 1 and y0 + y1 <= img_h + 1:
        nx0 = x0 / img_w
        ny0 = y0 / img_h
        nx1 = (x0 + x1) / img_w
        ny1 = (y0 + y1) / img_h
        if nx1 > nx0 and ny1 > ny0:
            return _clamp(nx0), _clamp(ny0), _clamp(nx1), _clamp(ny1)

    return None


def parse_glm_layout_details(
    payload_or_json: Any, page_index: int = 0
) -> GroundedResponse:
    """Parse ``layout_details`` emitted by GLM-OCR / vLLM.

    Each block has ``bbox_2d: [x0, y0, x1, y1]`` in pixel coords
    relative to the rendered page image. Accepts either the full JSON
    object or a pre-parsed dict. ``page_index`` specifies which page
    the blocks belong to (single-page calls).
    """
    if isinstance(payload_or_json, str):
        payload_or_json = json.loads(payload_or_json)
    d = payload_or_json

    pages = d.get("data_info", {}).get("pages", [])
    page_sizes = [(int(p["width"]), int(p["height"])) for p in pages]
    if not page_sizes:
        raise ValueError("parse_glm_layout_details: missing data_info.pages")

    # layout_details can be list[list[block]] (per page) or flat list.
    raw = d.get("layout_details", [])
    if raw and isinstance(raw[0], list):
        raw_blocks = raw[page_index] if page_index < len(raw) else []
    else:
        raw_blocks = raw

    blocks: list[GroundedBlock] = []
    pw, ph = page_sizes[page_index]
    for b in raw_blocks:
        # F1.16 audit fix: GLM's layout response uses a structural
        # label set ("text", "image", "table", "equation", ...) and the
        # old ``b.get("label") != "text"`` filter was a strict-equality
        # test that silently dropped any new label the upstream schema
        # added (e.g. "list_item", "form_field", "diagram_node"). We
        # now deny-list the known structural labels in
        # :data:`_GLM_STRUCTURAL_LABELS` instead, so any new content
        # label flows through the parser. A ``label`` field is also
        # optional (the older fixtures omitted it), so absence is not
        # a drop signal.
        label = b.get("label")
        if label is not None and label in _GLM_STRUCTURAL_LABELS:
            continue
        content = (b.get("content") or "").strip()
        if not content:
            continue
        # C9 hardening: bbox keys vary across GLM schema versions and
        # malformed payloads used to raise bare KeyError/ValueError and
        # abort the whole parse. Alias + guard + structured drop event.
        bbox_raw = b.get("bbox_2d") or b.get("bbox") or b.get("box")
        if not isinstance(bbox_raw, (list, tuple)) or len(bbox_raw) != 4:
            _emit_drop(page_index, "drop:missing_bbox")
            continue
        try:
            x0, y0, x1, y1 = (float(v) for v in bbox_raw)
        except (TypeError, ValueError):
            _emit_drop(page_index, "drop:bad_bbox")
            continue
        if not all(math.isfinite(v) for v in (x0, y0, x1, y1)):
            # NaN/inf would poison _clamp and the normalized bbox contract.
            _emit_drop(page_index, "drop:bad_bbox")
            continue
        blocks.append(
            GroundedBlock(
                bbox=[
                    _clamp(x0 / pw),
                    _clamp(y0 / ph),
                    _clamp(x1 / pw),
                    _clamp(y1 / ph),
                ],
                text=content,
                page_index=page_index,
            )
        )
    return GroundedResponse(blocks=blocks, page_sizes=page_sizes)


def _emit_drop(page_index: int, decision: str) -> None:
    """One structured drop event per malformed block (ops visibility)."""
    _emit_quality_event(
        "parsers",
        doc_id="-",
        page=page_index,
        duration_ms=0,
        decision=decision,
        fallback_used=False,
    )


def _recover_truncated_json_array(text: str) -> Any | None:
    """Attempt to recover complete objects from an abruptly truncated JSON array."""
    idx = text.rfind("}")
    if idx == -1:
        return None
    candidate = text[: idx + 1].strip()
    if candidate.startswith("["):
        candidate = candidate + "]"
    else:
        open_bracket = candidate.find("[")
        if open_bracket != -1:
            candidate = candidate[open_bracket:] + "]"
        else:
            return None
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def _parse_grounded_json(
    text: str,
    page_idx: int,
    img_w: int,
    img_h: int,
) -> list[GroundedBlock]:
    """Extract a JSON array of ``{bbox_2d, content}`` blocks from a VLM response.

    Handles three observed response shapes:
      1. Bare JSON array (Qwen3-VL)
      2. JSON wrapped in ```json ... ``` fence (Qwen2.5-VL)
      3. JSON with preamble prose before the array

    Per-item tolerance:
      - bbox key aliases: ``bbox_2d``, ``bbox``, ``box_2d``, ``box``,
        ``bounding_box``, ``coordinates`` (first match wins).
      - content key aliases: ``content``, ``text``, ``label_text``,
        ``ocr_text`` (first match wins).
      - coordinate spaces: pixel XYXY (canonical), already-normalized
        0..1, and XYWH ``[x, y, w, h]`` (auto-detected when the literal
        XYXY read would land outside the image).
      - dropped items are logged at INFO with reason counts so the
        common "everything comes back empty" failure mode is easy to
        spot in server logs.
    """
    raw = text.strip()
    if not raw:
        return []

    # Strip code fence if present.
    m = _JSON_FENCE.search(raw)
    if m:
        raw = m.group(1)
    elif raw.startswith("```"):
        # Defensive: open fence but closing dropped by truncation.
        raw = raw.lstrip("`").lstrip("json").lstrip().rstrip("`").rstrip()

    # Try a direct parse; fall back to greediest array substring and truncated recovery.
    data: Any
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        m2 = _BARE_ARRAY.search(raw)
        if m2:
            try:
                data = json.loads(m2.group(1))
            except json.JSONDecodeError:
                data = _recover_truncated_json_array(raw)
        else:
            data = _recover_truncated_json_array(raw)

        if data is None:
            log_grounded_parse_failure(
                raw, page_idx, ValueError("no valid array found in response")
            )
            return []

    if isinstance(data, dict):
        # Some models wrap the array in {"results": [...]} or similar.
        for key in ("results", "blocks", "layout", "layout_details", "items"):
            if key in data and isinstance(data[key], list):
                data = data[key]
                break
        else:
            data = [data]  # single object → one-element list

    if not isinstance(data, list):
        return []

    blocks: list[GroundedBlock] = []
    dropped = 0
    drop_reasons: dict[str, int] = {}
    for item in data:
        if not isinstance(item, dict):
            dropped += 1
            drop_reasons["not_dict"] = drop_reasons.get("not_dict", 0) + 1
            continue
        bbox = _extract_bbox(item)
        if bbox is None:
            dropped += 1
            drop_reasons["missing_or_bad_bbox"] = (
                drop_reasons.get("missing_or_bad_bbox", 0) + 1
            )
            logger.debug(
                "grounded parser: page %d dropped item (no 4-element bbox): %r",
                page_idx,
                {k: v for k, v in item.items() if k != "content"},
            )
            continue
        content = ""
        for key in _CONTENT_KEYS:
            if key in item and item[key] is not None:
                content = str(item[key])
                break
        content = content.strip()
        if not content:
            dropped += 1
            drop_reasons["empty_content"] = drop_reasons.get("empty_content", 0) + 1
            continue
        norm = _normalize_bbox(bbox, img_w, img_h)
        if norm is None:
            dropped += 1
            drop_reasons["invalid_bbox_coords"] = (
                drop_reasons.get("invalid_bbox_coords", 0) + 1
            )
            logger.debug(
                "grounded parser: page %d dropped item (bbox %r could not be "
                "normalized against image %dx%d)",
                page_idx,
                bbox,
                img_w,
                img_h,
            )
            continue
        blocks.append(
            GroundedBlock(
                bbox=[norm[0], norm[1], norm[2], norm[3]],
                text=content,
                page_index=page_idx,
            )
        )

    if dropped and logger.isEnabledFor(logging.INFO):
        logger.info(
            "grounded parser: page %d kept %d blocks, dropped %d (%s)",
            page_idx,
            len(blocks),
            dropped,
            ", ".join(f"{k}={v}" for k, v in sorted(drop_reasons.items())),
        )
    return blocks


def log_grounded_parse_failure(text: str, page_idx: int, exc: BaseException) -> None:
    """Public hook for callers that catch a parse failure upstream.

    Surfaces a warning when the grounded response payload is not
    parseable, so an operator can spot a regression in the LLM's
    response shape.
    """
    logger.warning(
        "Grounded bbox JSON parsing failed on page %d: %s — raw=%r",
        page_idx,
        exc,
        text[:200],
    )


__all__ = [
    "_BARE_ARRAY",
    "_JSON_FENCE",
    "_NON_CONTENT_LABELS",
    "_clamp",
    "_parse_grounded_json",
    "log_grounded_parse_failure",
    "parse_glm_layout_details",
]
