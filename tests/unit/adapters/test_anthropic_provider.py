import json

from agenttrace.adapters.providers import anthropic as adapter
from agenttrace.models import UsageSource


def _body(**kwargs: object) -> bytes:
    return json.dumps(kwargs).encode()


def test_detect_by_path() -> None:
    assert adapter.detect("/v1/messages", {}) is True


def test_detect_by_header() -> None:
    assert adapter.detect("/anything", {"anthropic-version": "2023-06-01"}) is True


def test_detect_false() -> None:
    assert adapter.detect("/v1/chat/completions", {}) is False


def test_normalize_request_basic() -> None:
    body = _body(
        model="claude-sonnet-4-6",
        messages=[{"role": "user", "content": "hello"}],
        system="You are helpful",
    )
    canonical = adapter.normalize_request(body)
    assert canonical.model == "claude-sonnet-4-6"
    assert len(canonical.messages) == 1
    assert canonical.messages[0].role == "user"
    assert canonical.system == [{"type": "text", "text": "You are helpful"}]


def test_normalize_request_malformed() -> None:
    canonical = adapter.normalize_request(b"not json{{{")
    assert canonical.model is None
    assert canonical.messages == []


def test_parse_usage_from_response() -> None:
    body = json.dumps(
        {
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read_input_tokens": 30,
                "cache_creation_input_tokens": 10,
            }
        }
    ).encode()
    usage = adapter.parse_usage_from_response(body)
    assert usage is not None
    assert usage.input_tokens == 100
    assert usage.output_tokens == 50
    assert usage.cache_read_input_tokens == 30
    assert usage.usage_source == UsageSource.reported


def test_parse_usage_from_response_no_usage() -> None:
    body = json.dumps({"type": "message"}).encode()
    assert adapter.parse_usage_from_response(body) is None


def test_parse_usage_from_stream_frames_terminal() -> None:
    frames = [
        {"type": "message_start"},
        {
            "type": "message_delta",
            "usage": {
                "input_tokens": 80,
                "output_tokens": 20,
                "cache_read_input_tokens": 5,
                "cache_creation_input_tokens": 0,
            },
        },
        {"type": "message_stop"},
    ]
    usage = adapter.parse_usage_from_stream_frames(frames)
    assert usage.input_tokens == 80
    assert usage.usage_source == UsageSource.reported


def test_parse_usage_from_stream_frames_partial() -> None:
    frames = [{"type": "content_block_delta"}]
    usage = adapter.parse_usage_from_stream_frames(frames)
    assert usage.usage_source == UsageSource.partial
