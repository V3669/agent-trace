import json

from agenttrace.adapters.agents.claude_code import (
    VOLATILE_HASH_RE,
    classify_tool_calls,
    normalize_volatile,
)
from agenttrace.adapters.providers.anthropic import normalize_request
from agenttrace.models import CanonicalRequest, ToolEventType


def _make_canonical(system_text: str) -> CanonicalRequest:
    return normalize_request(
        json.dumps(
            {
                "model": "claude-sonnet-4-6",
                "system": system_text,
                "messages": [{"role": "user", "content": "do task"}],
            }
        ).encode()
    )


def test_volatile_hash_re_matches_real() -> None:
    # Simulated real Claude Code volatile hash token
    text = "You are Claude Code.\nabcdef1234567890\nDo tasks."
    assert VOLATILE_HASH_RE.search(text) is not None


def test_volatile_hash_re_no_match_short() -> None:
    text = "You are Claude Code.\nabc123\nDo tasks."
    # 6 chars — below 8 char floor, should not match
    assert VOLATILE_HASH_RE.search(text) is None


def test_normalize_volatile_strips_single_hash() -> None:
    canonical = _make_canonical("Instructions.\nabcdef1234567890\nMore text.")
    result = normalize_volatile(canonical)
    system_text = result.system[0]["text"]
    assert "abcdef1234567890" not in system_text
    assert "Instructions." in system_text


def test_normalize_volatile_no_strip_zero_matches() -> None:
    canonical = _make_canonical("No hash here.")
    result = normalize_volatile(canonical)
    assert result.system[0]["text"] == "No hash here."


def test_normalize_volatile_no_strip_multiple_matches() -> None:
    canonical = _make_canonical("First.\nabcdef12345678\nSecond.\n1234567890abcdef\nEnd.")
    result = normalize_volatile(canonical)
    system_text = result.system[0]["text"]
    # Both hashes must remain (ambiguous → no strip)
    assert "abcdef12345678" in system_text
    assert "1234567890abcdef" in system_text


def test_classify_tool_calls_file_read() -> None:
    canonical = normalize_request(
        json.dumps(
            {
                "model": "claude-sonnet-4-6",
                "messages": [
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "tu_001",
                                "name": "Read",
                                "input": {"path": "/foo/bar.py"},
                            }
                        ],
                    }
                ],
            }
        ).encode()
    )
    events = classify_tool_calls(canonical)
    assert len(events) == 1
    assert events[0].event_type == ToolEventType.FILE_READ
    assert events[0].path == "/foo/bar.py"


def test_classify_tool_calls_edit() -> None:
    canonical = normalize_request(
        json.dumps(
            {
                "model": "claude-sonnet-4-6",
                "messages": [
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "tu_002",
                                "name": "Edit",
                                "input": {"path": "/x.py"},
                            }
                        ],
                    }
                ],
            }
        ).encode()
    )
    events = classify_tool_calls(canonical)
    assert events[0].event_type == ToolEventType.EDIT


def test_classify_tool_calls_unknown() -> None:
    canonical = normalize_request(
        json.dumps(
            {
                "model": "claude-sonnet-4-6",
                "messages": [
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "tu_003",
                                "name": "BashTool",
                                "input": {},
                            }
                        ],
                    }
                ],
            }
        ).encode()
    )
    events = classify_tool_calls(canonical)
    assert events[0].event_type == ToolEventType.OTHER
