"""Frequency statistics over token lists."""

from __future__ import annotations

import math
from collections import Counter


def word_freq(tokens: list[str]) -> dict[str, int]:
    """Token -> count."""
    counts: Counter[str] = Counter()
    for t in tokens:
        counts[t] += 1
    return dict(counts)


def top_k(freq: dict[str, int], k: int) -> list[tuple[str, int]]:
    """The ``k`` most frequent tokens, ties broken alphabetically."""
    items = sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))
    return items[:k]


def ngrams(tokens: list[str], n: int) -> list[tuple[str, ...]]:
    """All contiguous n-grams, in order."""
    if n <= 0:
        raise ValueError("n must be positive")
    return [tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)]


def tfidf(docs: list[list[str]]) -> list[dict[str, float]]:
    """TF-IDF per document. tf = count / len(doc); idf = log(N / df) (natural log, no smoothing)."""
    n_docs = len(docs)
    df: Counter[str] = Counter()
    for d in docs:
        for t in set(d):
            df[t] += 1
    out: list[dict[str, float]] = []
    for d in docs:
        counts = word_freq(d)
        total = len(d) or 1
        out.append({t: (c / total) * math.log(n_docs / df[t]) for t, c in counts.items()})
    return out
