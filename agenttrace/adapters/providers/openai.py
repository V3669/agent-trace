"""OpenAI provider adapter.

Handles wire-schema normalization and usage parsing for the OpenAI API format,
including both chat completions (/v1/chat/completions) and responses (/v1/responses).
Streaming usage requires the client to set stream_options.include_usage=true;
without it the stream returns no usage and is marked partial (backfilled later).
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from agenttrace.models import (
    CanonicalMessage,
    CanonicalRequest,
    EstimateConfidence,
    Usage,
    UsageSource,
)

logger = structlog.get_logger(__name__)


def detect(path: str, headers: dict[str, str]) -> bool:  # noqa: ARG001
    """Return True if this request is destined for the OpenAI provider."""
    path_l = path.lower()
    return "/v1/chat/completions" in path_l or "/v1/responses" in path_l


def normalize_request(body: bytes) -> CanonicalRequest:
    """Parse OpenAI request body into canonical form.

    System-role messages are hoisted into the system blocks list.
    Assistant tool_calls and tool-role messages are preserved as
    typed content blocks so downstream agent adapters can inspect them.
    """
    try:
        raw: dict[str, Any] = json.loads(body)
    except json.JSONDecodeError:
        logger.warning("openai.normalize.json_decode_error")
        return CanonicalRequest(raw={})

    messages_raw: list[dict[str, Any]] = raw.get("messages", [])
    system_blocks: list[dict[str, Any]] = []
    messages: list[CanonicalMessage] = []

    for m in messages_raw:
        role = m.get("role", "")
        if role == "system":
            system_blocks.extend(_extract_system_blocks(m.get("content", "")))
            continue

        content_blocks = _normalize_user_content(m.get("content", []))

        # Preserve tool_calls in assistant messages as a typed block.
        if role == "assistant":
            tool_calls = m.get("tool_calls")
            if tool_calls:
                content_blocks.append(
                    {"type": "tool_calls", "tool_calls": tool_calls}
                )

        # Tool results carry tool_call_id at the message level.
        if role == "tool":
            tool_call_id = m.get("tool_call_id", "")
            raw_content = m.get("content", "")
            content_blocks = [
                {
                    "type": "tool_result_openai",
                    "tool_call_id": tool_call_id,
                    "content": raw_content,
                }
            ]

        messages.append(CanonicalMessage(role=role, content=content_blocks))

    return CanonicalRequest(
        model=raw.get("model"),
        system=system_blocks,
        messages=messages,
        tools=_normalize_tools(raw.get("tools", [])),
        raw=raw,
    )


def _extract_system_blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [block for block in content if isinstance(block, dict)]
    return []


def _normalize_user_content(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [block for block in content if isinstance(block, dict)]
    return []


def _normalize_tools(tools: Any) -> list[dict[str, Any]]:
    if not isinstance(tools, list):
        return []
    return [t for t in tools if isinstance(t, dict)]


def parse_usage_from_response(response_body: bytes) -> Usage | None:
    """Extract usage from a non-streaming OpenAI response."""
    try:
        data: dict[str, Any] = json.loads(response_body)
    except json.JSONDecodeError:
        return None
    return _extract_usage(data)


def parse_usage_from_stream_frames(frames: list[dict[str, Any]]) -> Usage:
    """Accumulate usage from OpenAI SSE stream frames.

    OpenAI includes usage only in the final chunk when the client sets
    stream_options.include_usage=true.  If no usage frame is found, the
    stream is marked partial (will be backfilled by tokens/backfill.py).
    """
    input_tokens = 0
    output_tokens = 0
    cached_tokens = 0
    found_any = False

    for frame in frames:
        usage_data = frame.get("usage")
        if not usage_data or not isinstance(usage_data, dict):
            continue
        found_any = True
        input_tokens = usage_data.get("prompt_tokens", input_tokens)
        output_tokens = usage_data.get("completion_tokens", output_tokens)
        details = usage_data.get("prompt_tokens_details")
        if isinstance(details, dict):
            cached_tokens = details.get("cached_tokens", cached_tokens)

    if not found_any:
        return Usage(
            usage_source=UsageSource.partial,
            estimate_confidence=EstimateConfidence.low,
        )

    return Usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_input_tokens=cached_tokens,
        usage_source=UsageSource.reported,
    )


def _extract_usage(data: dict[str, Any]) -> Usage | None:
    usage_data = data.get("usage")
    if not usage_data or not isinstance(usage_data, dict):
        return None

    cached_tokens = 0
    details = usage_data.get("prompt_tokens_details")
    if isinstance(details, dict):
        cached_tokens = details.get("cached_tokens", 0)

    return Usage(
        input_tokens=usage_data.get("prompt_tokens", 0),
        output_tokens=usage_data.get("completion_tokens", 0),
        cache_read_input_tokens=cached_tokens,
        usage_source=UsageSource.reported,
    )
