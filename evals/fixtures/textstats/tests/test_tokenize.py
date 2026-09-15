from textstats import DEFAULT_STOPWORDS, normalize, tokenize


def test_tokenize_basic():
    assert tokenize("Hello, World! It's 2024.") == ["hello", "world", "it's", "2024"]


def test_tokenize_none_and_empty():
    assert tokenize(None) == []
    assert tokenize("") == []


def test_normalize_removes_stopwords():
    assert normalize(["the", "cat", "and", "a", "dog"]) == ["cat", "dog"]


def test_normalize_custom_stopwords_and_min_len():
    assert normalize(["x", "cat", "dog"], stopwords={"dog"}, min_len=2) == ["cat"]


def test_default_stopwords_frozen():
    assert "the" in DEFAULT_STOPWORDS and isinstance(DEFAULT_STOPWORDS, frozenset)
