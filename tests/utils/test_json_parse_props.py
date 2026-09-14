"""Property-based tests for :func:`omniscribe.utils.json_parse.extract_json`."""

from __future__ import annotations

import json
from typing import Any

import pytest

from omniscribe.utils.json_parse import extract_json

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

# Recursive strategy for JSON-serializable structures.
_json_primitives = (
    st.none()
    | st.booleans()
    | st.integers(min_value=-1_000_000, max_value=1_000_000)
    | st.floats(allow_nan=False, allow_infinity=False, min_value=-1e9, max_value=1e9)
    | st.text(max_size=50)
)

_json_composite = st.recursive(
    _json_primitives,
    lambda children: (
        st.lists(children, max_size=8)
        | st.dictionaries(st.text(max_size=20), children, max_size=8)
    ),
    max_leaves=20,
).filter(lambda x: isinstance(x, (dict, list)))

# Strings with tricky characters that could confuse naive brace counting.
_tricky_strings = st.text(
    alphabet=st.characters(
        whitelist_categories=["Lu", "Ll", "Nd", "P", "Zs"],
    )
    | st.sampled_from(["{", "}", "[", "]", ":", ",", '"', "'", "\\", "\n", "\t"]),
    max_size=40,
)

_tricky_json = st.dictionaries(
    keys=st.text(min_size=1, max_size=15),
    values=_tricky_strings | st.lists(_tricky_strings, max_size=4),
    max_size=6,
)


@settings(max_examples=100, deadline=None)
@given(data=_json_composite)
def test_valid_json_roundtrip(data: dict[str, Any] | list[Any]) -> None:
    """extract_json recovers an equal data structure from json.dumps."""
    serialized = json.dumps(data)
    recovered = extract_json(serialized)
    assert recovered == data


@settings(max_examples=100, deadline=None)
@given(
    data=_json_composite,
    leading_ws=st.text(alphabet=" \t\r\n", max_size=20),
    trailing_ws=st.text(alphabet=" \t\r\n", max_size=20),
)
def test_valid_json_surrounding_whitespace(
    data: dict[str, Any] | list[Any], leading_ws: str, trailing_ws: str
) -> None:
    """extract_json recovers valid JSON despite leading and trailing whitespace."""
    serialized = f"{leading_ws}{json.dumps(data)}{trailing_ws}"
    recovered = extract_json(serialized)
    assert recovered == data


@settings(max_examples=100, deadline=None)
@given(
    data=_json_composite,
    fence_tag=st.sampled_from(["json", "JSON", "Json", ""]),
    leading_ws=st.text(alphabet=" \t\r\n", max_size=10),
    trailing_ws=st.text(alphabet=" \t\r\n", max_size=10),
)
def test_fenced_markdown_json_roundtrip(
    data: dict[str, Any] | list[Any],
    fence_tag: str,
    leading_ws: str,
    trailing_ws: str,
) -> None:
    """extract_json unwraps markdown code fences correctly."""
    serialized = f"{leading_ws}```{fence_tag}\n{json.dumps(data)}\n```{trailing_ws}"
    recovered = extract_json(serialized)
    assert recovered == data


@settings(max_examples=100, deadline=None)
@given(
    data=_json_composite,
    preamble=st.text(
        alphabet=st.characters(blacklist_characters="[{`"),
        max_size=30,
    ),
    postamble=st.text(max_size=30),
)
def test_json_embedded_in_text(
    data: dict[str, Any] | list[Any],
    preamble: str,
    postamble: str,
) -> None:
    """extract_json finds the first valid JSON object/array embedded in text."""
    serialized = f"{preamble}\n{json.dumps(data)}\n{postamble}"
    recovered = extract_json(serialized)
    assert recovered == data


@settings(max_examples=100, deadline=None)
@given(data=_tricky_json)
def test_json_with_braces_in_strings(data: dict[str, Any]) -> None:
    """extract_json correctly preserves braces, brackets, and quotes in strings."""
    serialized = json.dumps(data)
    recovered = extract_json(serialized)
    assert recovered == data


@settings(max_examples=100, deadline=None)
@given(text=st.text(max_size=200))
def test_random_strings_handled_gracefully(text: str) -> None:
    """extract_json never crashes on arbitrary text; returns None or valid container."""
    result = extract_json(text)
    assert result is None or isinstance(result, (dict, list))


@settings(max_examples=100, deadline=None)
@given(
    corrupt_prefix=st.sampled_from(["{", "[", '{"key":', "[1, 2,", "{foo:"]),
    corrupt_body=st.text(max_size=50),
    corrupt_suffix=st.sampled_from(["", "}", "]", ",]", "}}"]),
)
def test_malformed_syntax_handled_gracefully(
    corrupt_prefix: str, corrupt_body: str, corrupt_suffix: str
) -> None:
    """extract_json handles malformed syntax without unhandled exceptions."""
    text = f"{corrupt_prefix}{corrupt_body}{corrupt_suffix}"
    result = extract_json(text)
    assert result is None or isinstance(result, (dict, list))


@settings(max_examples=100, deadline=None)
@given(
    data=_json_composite,
    junk=st.lists(st.sampled_from(["}", "]", "text", '"quote"', ":"]), max_size=12),
)
def test_json_with_leading_closer_junk(
    data: dict[str, Any] | list[Any], junk: list[str]
) -> None:
    """Junk without openers before the payload never hides it."""
    text = " ".join(junk) + " " + json.dumps(data)
    assert extract_json(text) == data


@settings(max_examples=100, deadline=None)
@given(
    data=_json_composite,
    words=st.lists(st.text(alphabet="abc :,", max_size=8), max_size=6),
)
def test_json_after_unclosed_quote(
    data: dict[str, Any] | list[Any], words: list[str]
) -> None:
    """An unbalanced prose quote before the payload still yields the payload."""
    text = '"unclosed ' + " ".join(words) + " " + json.dumps(data)
    assert extract_json(text) == data


@settings(max_examples=100, deadline=None)
@given(
    data=_json_composite,
    junk=st.lists(
        st.sampled_from(['{"a":', "[", "}", "]", "text", '"q"']), max_size=12
    ),
)
def test_json_with_trailing_junk(
    data: dict[str, Any] | list[Any], junk: list[str]
) -> None:
    """Trailing junk after the payload never changes the extraction."""
    text = json.dumps(data) + " " + " ".join(junk)
    assert extract_json(text) == data
