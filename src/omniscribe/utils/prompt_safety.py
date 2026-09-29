"""Defensive sanitization helpers for user/external text interpolated into prompts.

LLM prompt templates interpolate text from several sources, including
the extraction ``custom_prompt`` (end-user controlled) and OCR draft
output (system-generated but still external). Without sanitization, a
crafted input can:

- Inject a fake instruction boundary (``--- CUSTOM INSTRUCTION END ---``)
  to truncate or extend the controlled prompt region.
- Hide null bytes / control characters that confuse the chat template
  tokenizer or hide a forged turn boundary.
- Blow up the prompt budget with an unbounded payload, evicting the
  real schema / document content from the model's context window.

The functions in this module are deliberately conservative — they only
do things that have no plausible downside for legitimate inputs. We
**never** try to detect "malicious" content by content pattern (that
is a model-side problem); we only normalize shape.

See refactor §2.6 (preserved in Git history).
"""

from __future__ import annotations

import re
import unicodedata

# Conservative default cap. The longest legitimate custom instruction
# the team has seen fits comfortably in 4 KiB; anything beyond that is
# almost certainly junk or an attempt to evict the schema from the
# context window. 16 KiB is generous and still well under the prompt
# budget of any reasonable extraction call.
DEFAULT_MAX_CHARS = 16 * 1024

# Strip ASCII control chars (except common whitespace) plus the BOM and
# zero-width characters that frequently sneak in from copy/paste. The
# set is intentionally explicit; the unicode "control" category also
# catches C1 controls we want to drop.
_CONTROL_CHARS = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\ufeff\u200b-\u200d\u2060\u2028]"
)

# Whitespace runs longer than 2 collapse to a single space. Long runs of
# spaces / tabs can hide intent from log reviewers and bloat tokens.
_WS_RUN = re.compile(r"[ \t]{3,}")

# Boundary markers used by the prompt templates. If a user-controlled
# input contains the closing marker for a region, the LLM may treat the
# post-marker text as a new controlled instruction. Replace each
# occurrence with a visually similar but distinguishable string.
_BOUNDARY_REPLACEMENTS = (
    ("--- CUSTOM INSTRUCTION START ---", "--- CUSTOM INSTRUCTION START- -"),
    ("--- CUSTOM INSTRUCTION END ---", "--- CUSTOM INSTRUCTION END- -"),
)


def sanitize_prompt_input(
    text: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> str:
    """Return ``text`` normalized for safe interpolation into a prompt.

    Operations applied, in order:

    1. Replace boundary markers so an attacker cannot fake the closing
       of a controlled region by including the literal marker text.
    2. Strip ASCII / Unicode control characters and zero-width spaces.
    3. Collapse long whitespace runs.
    4. Unicode-normalize (NFKC) so visually-equivalent characters don't
       silently change the prompt's meaning.
    5. Truncate to ``max_chars`` and append an ellipsis marker when the
       input was longer, so the model sees that content was elided.
    """
    if not text:
        return ""

    cleaned = text
    for marker, replacement in _BOUNDARY_REPLACEMENTS:
        cleaned = cleaned.replace(marker, replacement)

    cleaned = unicodedata.normalize("NFKC", cleaned)
    cleaned = _CONTROL_CHARS.sub("", cleaned)
    cleaned = _WS_RUN.sub("  ", cleaned)

    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars] + " …[truncated]"

    return cleaned


__all__ = [
    "DEFAULT_MAX_CHARS",
    "sanitize_prompt_input",
]
