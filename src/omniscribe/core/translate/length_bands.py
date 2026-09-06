"""Script-aware length-ratio bands for translation sanity checks.

Flat char-ratio bands (0.1-2.5) misfire across scripts: English→CJK
typically *shrinks* 2-4x in chars and CJK→English *expands* 2-4x, so a
faithful translation trips "too long"/"too short" and burns retries.
The band is chosen from the observed script pair instead.
"""

from __future__ import annotations

import re

# Continuous CJK runs (scripts where char counts don't map 1:1 to
# English char counts).
_CJK_RE = re.compile(
    r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af]"
)
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


def effective_band(source: str, translated: str) -> tuple[float, float]:
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
