"""Fixture harness for the chrF offline translation eval (not a CI gate)."""

from __future__ import annotations

import pytest

from omniscribe.core.translate.eval_chrf import chrf


def test_identical_is_one() -> None:
    assert chrf("bonjour le monde", "bonjour le monde") == pytest.approx(1.0)


def test_disjoint_is_zero() -> None:
    # No shared characters at all (a shared space would already give a
    # small nonzero chrF under the standard definition).
    assert chrf("abcdef", "xyzuvw") == pytest.approx(0.0)


def test_empty_inputs_are_zero() -> None:
    assert chrf("", "x") == 0.0
    assert chrf("x", "") == 0.0


def test_good_beats_bad() -> None:
    ref = "Le comité se réunit le 3 mars 2024 à Bruxelles."
    good = "Le comité se réunit le 3 mars 2024 à Bruxelles."
    bad = "The committee meets in March."
    assert chrf(ref, good) > chrf(ref, bad)


def test_partial_overlap_between_zero_and_one() -> None:
    score = chrf("bonjour le monde", "bonjour le monde entier")
    assert 0.0 < score < 1.0
