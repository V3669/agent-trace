import json

from agenttrace.adapters.agents.claude_code import extract_file_payloads
from agenttrace.adapters.providers.anthropic import normalize_request


def _make_body_with_tool_result(path: str, content: str) -> bytes:
    return json.dumps(
        {
            "model": "claude-sonnet-4-6",
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tu_read_001",
                            "name": "Read",
                            "input": {"path": path},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tu_read_001",
                            "content": content,
                        }
                    ],
                },
            ],
        }
    ).encode()


def test_extract_file_payload_string_content() -> None:
    canonical = normalize_request(_make_body_with_tool_result("/app/main.py", "def hello(): pass"))
    events = extract_file_payloads(canonical)
    assert len(events) == 1
    assert events[0].path == "/app/main.py"
    assert events[0].content == "def hello(): pass"
    assert len(events[0].content_hash) == 64  # SHA-256 hex
    assert events[0].approx_tokens > 0


def test_extract_file_payload_list_content() -> None:
    body = json.dumps(
        {
            "model": "claude-sonnet-4-6",
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tu_002",
                            "name": "Read",
                            "input": {"path": "/x.py"},
                        },
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tu_002",
                            "content": [{"type": "text", "text": "print('hi')"}],
                        }
                    ],
                },
            ],
        }
    ).encode()
    canonical = normalize_request(body)
    events = extract_file_payloads(canonical)
    assert len(events) == 1
    assert "print('hi')" in events[0].content


def test_no_events_when_no_tool_results() -> None:
    body = json.dumps(
        {
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "hi"}],
        }
    ).encode()
    canonical = normalize_request(body)
    events = extract_file_payloads(canonical)
    assert events == []


def test_non_file_read_tool_ignored() -> None:
    body = json.dumps(
        {
            "model": "claude-sonnet-4-6",
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "tu_003", "name": "BashTool", "input": {}},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "tu_003", "content": "output"},
                    ],
                },
            ],
        }
    ).encode()
    canonical = normalize_request(body)
    events = extract_file_payloads(canonical)
    assert events == []
