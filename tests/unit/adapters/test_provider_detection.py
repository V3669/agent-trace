from agenttrace.adapters.providers.detection import detect_provider
from agenttrace.models import Provider


def test_detect_anthropic_by_path() -> None:
    assert detect_provider("/v1/messages", {}) == Provider.anthropic


def test_detect_anthropic_by_header() -> None:
    assert detect_provider("/", {"anthropic-version": "2023-06-01"}) == Provider.anthropic


def test_detect_openai_chat() -> None:
    assert detect_provider("/v1/chat/completions", {}) == Provider.openai


def test_detect_openai_responses() -> None:
    assert detect_provider("/v1/responses", {}) == Provider.openai


def test_detect_unknown() -> None:
    assert detect_provider("/health", {}) == Provider.unknown
