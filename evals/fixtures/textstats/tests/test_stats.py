import math

import pytest

from textstats import ngrams, tfidf, top_k, word_freq


def test_word_freq():
    assert word_freq(["a", "b", "a"]) == {"a": 2, "b": 1}
    assert word_freq([]) == {}


def test_top_k_order_and_ties():
    freq = {"b": 2, "a": 2, "c": 5, "d": 1}
    assert top_k(freq, 3) == [("c", 5), ("a", 2), ("b", 2)]
    assert top_k(freq, 10) == [("c", 5), ("a", 2), ("b", 2), ("d", 1)]


def test_ngrams_counts():
    toks = ["a", "b", "c", "d"]
    assert ngrams(toks, 1) == [("a",), ("b",), ("c",), ("d",)]
    assert ngrams(toks, 2) == [("a", "b"), ("b", "c"), ("c", "d")]
    assert ngrams(toks, 4) == [("a", "b", "c", "d")]
    assert ngrams(toks, 5) == []


def test_ngrams_rejects_zero():
    with pytest.raises(ValueError):
        ngrams(["a"], 0)


def test_tfidf_values():
    docs = [["a", "b", "a"], ["b", "c"]]
    out = tfidf(docs)
    # "a" appears only in doc 0: tf = 2/3, idf = ln(2/1)
    assert out[0]["a"] == pytest.approx((2 / 3) * math.log(2))
    # "b" appears in both docs -> idf = 0
    assert out[0]["b"] == pytest.approx(0.0)
    assert out[1]["c"] == pytest.approx(0.5 * math.log(2))
    assert out[1]["c"] > 0


def test_tfidf_empty_doc():
    assert tfidf([[], ["x"]]) == [{}, {"x": pytest.approx(math.log(2))}]
