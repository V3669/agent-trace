"""Unit tests for the Aider agent adapter."""

from __future__ import annotations

import json

from agenttrace.adapters.agents import aider as adapter
from agenttrace.adapters.providers import openai as openai_adapter
from agenttrace.models import CanonicalRequest, ToolEventType


def _make_canonical(messages_raw: list[dict]) -> CanonicalRequest:
    """Build a CanonicalRequest via the OpenAI normalizer (realistic pipeline)."""
    body = json.dumps({"model": "gpt-4o", "messages": messages_raw}).encode()
    return openai_adapter.normalize_request(body)


class TestNormalizeVolatile:
    def test_passthrough_unchanged(self) -> None:
        req = CanonicalRequest(
            model="gpt-4o",
            system=[{"type": "text", "text": "You are a coding assistant."}],
        )
        result = adapter.normalize_volatile(req)
        assert result is req  # same object; no copy needed


class TestClassifyToolCalls:
    def test_file_read_detected(self) -> None:
        canonical = _make_canonical(
            [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": '{"path": "foo.py"}',
                            },
                        }
                    ],
                }
            ]
        )
        events = adapter.classify_tool_calls(canonical)
        assert len(events) == 1
        assert events[0].event_type == ToolEventType.FILE_READ
        assert events[0].path == "foo.py"
        assert events[0].tool_use_id == "call_1"

    def test_glob_scan_detected(self) -> None:
        canonical = _make_canonical(
            [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call_2",
                            "type": "function",
                            "function": {
                                "name": "find_files",
                                "arguments": '{"pattern": "*.py"}',
                            },
                        }
                    ],
                }
            ]
        )
        events = adapter.classify_tool_calls(canonical)
        assert events[0].event_type == ToolEventType.GLOB_SCAN

    def test_edit_detected(self) -> None:
        canonical = _make_canonical(
            [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call_3",
                            "type": "function",
                            "function": {
                                "name": "write_file",
                                "arguments": '{"path": "bar.py", "content": "x=1"}',
                            },
                        }
                    ],
                }
            ]
        )
        events = adapter.classify_tool_calls(canonical)
        assert events[0].event_type == ToolEventType.EDIT

    def test_unknown_tool_classified_other(self) -> None:
        canonical = _make_canonical(
            [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call_4",
                            "type": "function",
                            "function": {"name": "run_tests", "arguments": "{}"},
                        }
                    ],
                }
            ]
        )
        events = adapter.classify_tool_calls(canonical)
        assert events[0].event_type == ToolEventType.OTHER

    def test_no_tool_calls_returns_empty(self) -> None:
        canonical = _make_canonical([{"role": "user", "content": "Hello"}])
        events = adapter.classify_tool_calls(canonical)
        assert events == []

    def test_multiple_tools_in_one_message(self) -> None:
        canonical = _make_canonical(
            [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": '{"path": "a.py"}',
                            },
                        },
                        {
                            "id": "c2",
                            "type": "function",
                            "function": {
                                "name": "write_file",
                                "arguments": '{"path": "b.py", "content": ""}',
                            },
                        },
                    ],
                }
            ]
        )
        events = adapter.classify_tool_calls(canonical)
        assert len(events) == 2
        assert events[0].event_type == ToolEventType.FILE_READ
        assert events[1].event_type == ToolEventType.EDIT


class TestExtractFilePayloads:
    def _build_read_result(
        self,
        call_id: str,
        path: str,
        file_content: str,
    ) -> CanonicalRequest:
        """Construct a canonical request that has a FILE_READ call + result."""
        return _make_canonical(
            [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": json.dumps({"path": path}),
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": file_content,
                },
            ]
        )

    def test_basic_file_read_extracted(self) -> None:
        canonical = self._build_read_result("call_1", "src/main.py", "print('hello')")
        events = adapter.extract_file_payloads(canonical)
        assert len(events) == 1
        assert events[0].path == "src/main.py"
        assert events[0].content == "print('hello')"
        assert len(events[0].content_hash) == 64  # SHA-256 hex

    def test_approx_tokens_populated(self) -> None:
        content = "x = 1\n" * 100
        canonical = self._build_read_result("c1", "big.py", content)
        events = adapter.extract_file_payloads(canonical)
        assert events[0].approx_tokens > 0

    def test_no_file_reads_returns_empty(self) -> None:
        canonical = _make_canonical([{"role": "user", "content": "Hi"}])
        events = adapter.extract_file_payloads(canonical)
        assert events == []

    def test_unmatched_tool_result_ignored(self) -> None:
        """A tool_result without a matching FILE_READ call is ignored."""
        canonical = _make_canonical(
            [
                {
                    "role": "tool",
                    "tool_call_id": "orphan_call",
                    "content": "some content",
                }
            ]
        )
        events = adapter.extract_file_payloads(canonical)
        assert events == []

    def test_edit_tool_not_extracted(self) -> None:
        """EDIT tool calls must not produce file read events."""
        canonical = _make_canonical(
            [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "c_edit",
                            "type": "function",
                            "function": {
                                "name": "write_file",
                                "arguments": '{"path": "out.py", "content": "x=1"}',
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "c_edit",
                    "content": "OK",
                },
            ]
        )
        events = adapter.extract_file_payloads(canonical)
        assert events == []

    def test_content_hash_deterministic(self) -> None:
        canonical = self._build_read_result("c1", "foo.py", "hello")
        ev1 = adapter.extract_file_payloads(canonical)
        ev2 = adapter.extract_file_payloads(canonical)
        assert ev1[0].content_hash == ev2[0].content_hash

    def test_different_content_different_hash(self) -> None:
        c1 = self._build_read_result("c1", "f.py", "version 1")
        c2 = self._build_read_result("c2", "f.py", "version 2")
        hash1 = adapter.extract_file_payloads(c1)[0].content_hash
        hash2 = adapter.extract_file_payloads(c2)[0].content_hash
        assert hash1 != hash2
