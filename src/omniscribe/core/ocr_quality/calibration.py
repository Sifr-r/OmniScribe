"""Confidence calibration for the OCR quality trust layer.

Platt-style single-parameter logistic: ``sigmoid(a * raw + b)``.
Parameters are loaded lazily from ``resources/calibration/{model_id}.json``
and cached for the process lifetime. Missing files degrade to identity
(``raw`` returned unchanged) with an info log — the trust layer never
blocks a job because of a missing calibration artefact.

The special model id ``"identity"`` is the canonical "no calibration"
sentinel — Platt scaling cannot be the identity function on ``[0, 1]``
(sigmoid never reaches its endpoints at finite ``a, b``), so we
short-circuit and return ``raw`` unchanged.
"""

from __future__ import annotations

import json
import logging
import math
from collections import OrderedDict
from pathlib import Path
from typing import Final

from .events import emit

_LOG = logging.getLogger(__name__)

_CALIBRATION_DIR: Final[Path] = (
    Path(__file__).resolve().parents[2] / "resources" / "calibration"
)

# F1.10 audit fix: bound the per-process cache. A long-running server
# processing many distinct model ids (e.g. a multi-tenant deployment
# or a constant A/B experiment churn) would grow this dict without
# limit. 1024 entries is a generous cap — model ids are short, so
# the cache footprint stays in the tens of KB even at saturation.
# LRU eviction preserves the hot working set.
_CACHE_MAX_SIZE: Final[int] = 1024
# Cached params per model id. ``None`` is the identity sentinel —
# ``calibrate`` returns ``raw`` unchanged without applying Platt.
_CACHE: OrderedDict[str, tuple[float, float] | None] = OrderedDict()


def _cache_put(model_id: str, params: tuple[float, float] | None) -> None:
    """Insert ``params`` for ``model_id`` with LRU eviction at the cap.

    ``OrderedDict.move_to_end`` is the standard idiom for LRU bookkeeping
    on Python 3.7+ (insertion order is the default iteration order);
    re-inserting an existing key moves it to the end, and ``popitem``
    on the front evicts the least-recently-used entry when we hit the
    cap. No external dependency, no thread-safety cost beyond what the
    GIL already provides for short dict ops.
    """
    if model_id in _CACHE:
        _CACHE.move_to_end(model_id)
    _CACHE[model_id] = params
    while len(_CACHE) > _CACHE_MAX_SIZE:
        _CACHE.popitem(last=False)


def _load_params(model_id: str) -> tuple[float, float] | None:
    started = _now_ms()
    if model_id in _CACHE:
        # Touch for LRU.
        _CACHE.move_to_end(model_id)
        return _CACHE[model_id]
    if model_id == "identity":
        _cache_put(model_id, None)
        return None
    path = _CALIBRATION_DIR / f"{model_id}.json"
    if not path.exists():
        _LOG.info(
            "calibration: no file for model_id=%r at %s — using identity",
            model_id,
            path,
        )
        _cache_put(model_id, None)
        emit(
            "calibration",
            doc_id=model_id,
            page=-1,
            duration_ms=_now_ms() - started,
            decision="identity",
            fallback_used=True,
        )
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        a = float(data["a"])
        b = float(data["b"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        _LOG.warning(
            "calibration: failed to load %s (%s) — using identity",
            path,
            exc,
        )
        _cache_put(model_id, None)
        emit(
            "calibration",
            doc_id=model_id,
            page=-1,
            duration_ms=_now_ms() - started,
            decision="load_error",
            fallback_used=True,
        )
        return None
    _cache_put(model_id, (a, b))
    return a, b


def calibrate(raw: float, model_id: str) -> float:
    """Return ``sigmoid(a*raw + b)`` clamped to ``[0, 1]``.

    For ``model_id == "identity"`` (or any unknown id without a JSON
    file), ``raw`` is returned unchanged.
    """
    if not 0.0 <= raw <= 1.0:
        # Inputs outside ``[0, 1]`` are clamped first — protects against
        # upstream callers that mix scaled confidences.
        raw = max(0.0, min(1.0, raw))
    params = _load_params(model_id)
    if params is None:
        return raw
    a, b = params
    z = a * raw + b
    # Numerically stable sigmoid.
    if z >= 0:
        s = 1.0 / (1.0 + math.exp(-z))
    else:
        ez = math.exp(z)
        s = ez / (1.0 + ez)
    return max(0.0, min(1.0, s))


def reset_cache() -> None:
    """Clear the in-process calibration cache (used by tests)."""
    _CACHE.clear()


__all__ = ["calibrate", "reset_cache"]


def _now_ms() -> int:
    import time

    return int(time.monotonic() * 1000)
