"""Unit tests for the OpenAI provider adapter."""

from __future__ import annotations

import json

from agenttrace.adapters.providers import openai as adapter
from agenttrace.models import EstimateConfidence, UsageSource


class TestDetect:
    def test_chat_completions_path(self) -> None:
        assert adapter.detect("/v1/chat/completions", {})

    def test_responses_path(self) -> None:
        assert adapter.detect("/v1/responses", {})

    def test_anthropic_path_rejected(self) -> None:
        assert not adapter.detect("/v1/messages", {})

    def test_unknown_path_rejected(self) -> None:
        assert not adapter.detect("/v1/models", {})

    def test_case_insensitive_path(self) -> None:
        assert adapter.detect("/V1/Chat/Completions", {})


class TestNormalizeRequest:
    def _body(self, payload: dict) -> bytes:
        return json.dumps(payload).encode()

    def test_basic_user_message(self) -> None:
        body = self._body(
            {
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Hello"}],
            }
        )
        result = adapter.normalize_request(body)
        assert result.model == "gpt-4o"
        assert len(result.messages) == 1
        assert result.messages[0].role == "user"
        assert result.messages[0].content == [{"type": "text", "text": "Hello"}]

    def test_system_message_hoisted(self) -> None:
        body = self._body(
            {
                "model": "gpt-4o",
                "messages": [
                    {"role": "system", "content": "You are helpful."},
                    {"role": "user", "content": "Hi"},
                ],
            }
        )
        result = adapter.normalize_request(body)
        # System message hoisted to system blocks, not in messages list.
        assert len(result.system) == 1
        assert result.system[0] == {"type": "text", "text": "You are helpful."}
        assert len(result.messages) == 1
        assert result.messages[0].role == "user"

    def test_tool_calls_preserved(self) -> None:
        tool_call = {
            "id": "call_abc",
            "type": "function",
            "function": {"name": "read_file", "arguments": '{"path": "foo.py"}'},
        }
        body = self._body(
            {
                "model": "gpt-4o",
                "messages": [
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [tool_call],
                    }
                ],
            }
        )
        result = adapter.normalize_request(body)
        assert len(result.messages) == 1
        assert result.messages[0].role == "assistant"
        tool_block = next(
            b for b in result.messages[0].content if b.get("type") == "tool_calls"
        )
        assert tool_block["tool_calls"] == [tool_call]

    def test_tool_result_message_normalized(self) -> None:
        body = self._body(
            {
                "model": "gpt-4o",
                "messages": [
                    {
                        "role": "tool",
                        "tool_call_id": "call_abc",
                        "content": "file content here",
                    }
                ],
            }
        )
        result = adapter.normalize_request(body)
        assert len(result.messages) == 1
        assert result.messages[0].role == "tool"
        block = result.messages[0].content[0]
        assert block["type"] == "tool_result_openai"
        assert block["tool_call_id"] == "call_abc"
        assert block["content"] == "file content here"

    def test_tools_preserved(self) -> None:
        tool_def = {"type": "function", "function": {"name": "read_file"}}
        body = self._body(
            {
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "hi"}],
                "tools": [tool_def],
            }
        )
        result = adapter.normalize_request(body)
        assert result.tools == [tool_def]

    def test_malformed_json_returns_empty(self) -> None:
        result = adapter.normalize_request(b"not json")
        assert result.model is None
        assert result.messages == []
        assert result.system == []

    def test_list_content_in_system(self) -> None:
        body = self._body(
            {
                "model": "gpt-4o",
                "messages": [
                    {
                        "role": "system",
                        "content": [{"type": "text", "text": "Be helpful."}],
                    }
                ],
            }
        )
        result = adapter.normalize_request(body)
        assert result.system == [{"type": "text", "text": "Be helpful."}]


class TestParseUsageFromResponse:
    def test_basic_usage(self) -> None:
        body = json.dumps(
            {
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                }
            }
        ).encode()
        usage = adapter.parse_usage_from_response(body)
        assert usage is not None
        assert usage.input_tokens == 100
        assert usage.output_tokens == 50
        assert usage.cache_read_input_tokens == 0
        assert usage.usage_source == UsageSource.reported

    def test_cached_tokens_extracted(self) -> None:
        body = json.dumps(
            {
                "usage": {
                    "prompt_tokens": 200,
                    "completion_tokens": 30,
                    "prompt_tokens_details": {"cached_tokens": 150},
                }
            }
        ).encode()
        usage = adapter.parse_usage_from_response(body)
        assert usage is not None
        assert usage.cache_read_input_tokens == 150

    def test_missing_usage_returns_none(self) -> None:
        body = json.dumps({"id": "chatcmpl-123"}).encode()
        usage = adapter.parse_usage_from_response(body)
        assert usage is None

    def test_malformed_json_returns_none(self) -> None:
        usage = adapter.parse_usage_from_response(b"not json")
        assert usage is None

    def test_null_usage_field_returns_none(self) -> None:
        body = json.dumps({"usage": None}).encode()
        usage = adapter.parse_usage_from_response(body)
        assert usage is None


class TestParseUsageFromStreamFrames:
    def test_final_chunk_with_usage(self) -> None:
        frames = [
            {"choices": [{"delta": {"content": "hello"}}], "usage": None},
            {
                "choices": [{"delta": {}}],
                "usage": {
                    "prompt_tokens": 80,
                    "completion_tokens": 20,
                },
            },
        ]
        usage = adapter.parse_usage_from_stream_frames(frames)
        assert usage.input_tokens == 80
        assert usage.output_tokens == 20
        assert usage.usage_source == UsageSource.reported

    def test_cached_tokens_in_stream(self) -> None:
        frames = [
            {
                "usage": {
                    "prompt_tokens": 300,
                    "completion_tokens": 10,
                    "prompt_tokens_details": {"cached_tokens": 200},
                }
            }
        ]
        usage = adapter.parse_usage_from_stream_frames(frames)
        assert usage.cache_read_input_tokens == 200

    def test_no_usage_frame_returns_partial(self) -> None:
        frames = [
            {"choices": [{"delta": {"content": "word"}}]},
            {"choices": [{"finish_reason": "stop"}]},
        ]
        usage = adapter.parse_usage_from_stream_frames(frames)
        assert usage.usage_source == UsageSource.partial
        assert usage.estimate_confidence == EstimateConfidence.low

    def test_empty_frames_returns_partial(self) -> None:
        usage = adapter.parse_usage_from_stream_frames([])
        assert usage.usage_source == UsageSource.partial
