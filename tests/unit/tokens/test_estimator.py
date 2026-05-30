from agenttrace.tokens.estimator import estimate_tokens


def test_estimate_tokens_returns_positive() -> None:
    assert estimate_tokens("hello world") > 0


def test_estimate_tokens_longer_text_more_tokens() -> None:
    short = estimate_tokens("hi")
    long = estimate_tokens("hi " * 100)
    assert long > short


def test_estimate_tokens_empty() -> None:
    # Should not raise; returns at least 1 per our fallback
    result = estimate_tokens("")
    assert isinstance(result, int)
