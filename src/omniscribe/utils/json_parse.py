import json
import re
from typing import Any

_FENCED_JSON_RE = re.compile(r"\A```(?:json)?\s*(.*?)\s*```\s*\Z", re.DOTALL | re.I)

# Caps that keep pathological LLM output from degrading into O(n^2) scanning.
_MAX_SPAN_ATTEMPTS = 250
_MAX_CANDIDATE_ATTEMPTS = 1000


def _balanced_spans(s: str) -> tuple[list[tuple[int, int]], bool]:
    """String-aware (open, close) index pairs of balanced {}/[] spans.

    ``dirty`` is True when an open bracket was skipped as string content, which
    means the span list is incomplete and per-candidate decoding must decide.
    """
    spans: list[tuple[int, int]] = []
    stack: list[tuple[int, str]] = []
    in_string = False
    escaped = False
    dirty = False
    for i, ch in enumerate(s):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            elif ch in "{[":
                dirty = True
        elif ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append((i, ch))
        elif ch in "}]" and stack:
            open_idx, open_ch = stack.pop()
            if (open_ch == "{") == (ch == "}"):
                spans.append((open_idx, i))
    return spans, dirty


def extract_json(text: str) -> Any:
    """Find the first parseable JSON object or array in the text."""
    stripped = text.strip()
    if not stripped:
        return None

    fenced = _FENCED_JSON_RE.match(stripped)
    candidate = fenced.group(1).strip() if fenced else stripped

    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, (dict, list)):
            return parsed
    except (json.JSONDecodeError, RecursionError):
        pass

    decoder = json.JSONDecoder()
    spans, dirty = _balanced_spans(stripped)
    if not dirty and not spans:
        return None
    if not dirty:
        # A clean scan saw every bracket outside strings, so the earliest
        # loadable span is what per-candidate decoding would find first.
        for start, end in sorted(spans)[:_MAX_SPAN_ATTEMPTS]:
            try:
                return json.loads(stripped[start : end + 1])
            except (json.JSONDecodeError, RecursionError):
                continue

    idx = 0
    for _ in range(_MAX_CANDIDATE_ATTEMPTS):
        brace_idx = stripped.find("{", idx)
        bracket_idx = stripped.find("[", idx)
        if brace_idx == -1 and bracket_idx == -1:
            break
        if brace_idx == -1:
            start = bracket_idx
        elif bracket_idx == -1:
            start = brace_idx
        else:
            start = min(brace_idx, bracket_idx)

        try:
            parsed, _end = decoder.raw_decode(stripped, idx=start)
            if isinstance(parsed, (dict, list)):
                return parsed
        except json.JSONDecodeError:
            pass
        idx = start + 1

    return None
