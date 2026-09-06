"""chrF — character n-gram F-score for offline translation eval.

Standard chrF (Popović 2015): F_beta with beta=2.0 averaged over
character n-grams (1..max_n) between reference and hypothesis. Used by
the fixture harness test only — not a CI gate.
"""

from __future__ import annotations

from collections import Counter


def _ngrams(text: str, n: int) -> Counter[str]:
    return Counter(text[i : i + n] for i in range(len(text) - n + 1))


def chrf(
    reference: str, hypothesis: str, *, max_n: int = 6, beta: float = 2.0
) -> float:
    """Character n-gram F-score in [0.0, 1.0]; 1.0 for identical strings."""
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
