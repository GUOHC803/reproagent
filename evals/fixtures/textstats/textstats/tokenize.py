"""Tokenization helpers."""

from __future__ import annotations

import re

DEFAULT_STOPWORDS = frozenset({"a", "an", "the", "of", "and", "or", "to", "in", "is", "it"})

_WORD = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")


def tokenize(text: str | None) -> list[str]:
    """Lower-case word tokens. ``None`` and empty strings yield an empty list."""
    if text is None:
        return []
    return _WORD.findall(text.lower())


def normalize(tokens: list[str], stopwords: frozenset[str] | set[str] | None = None, min_len: int = 1) -> list[str]:
    """Drop stopwords and tokens shorter than ``min_len``."""
    if stopwords is None:
        stopwords = DEFAULT_STOPWORDS
    return [t for t in tokens if t not in stopwords and len(t) >= min_len]
