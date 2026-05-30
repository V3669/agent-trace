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


def detect(path: str, headers: dict[str, str]) -> bool:
    path_l = path.lower()
    return "/v1/messages" in path_l or "anthropic-version" in {k.lower() for k in headers}


def normalize_request(body: bytes) -> CanonicalRequest:
    try:
        raw: dict[str, Any] = json.loads(body)
    except json.JSONDecodeError:
        return CanonicalRequest(raw={})

    messages = [
        CanonicalMessage(role=m.get("role", ""), content=_normalize_content(m.get("content", [])))
        for m in raw.get("messages", [])
    ]
    system_raw = raw.get("system", [])
    if isinstance(system_raw, str):
        system_raw = [{"type": "text", "text": system_raw}]

    return CanonicalRequest(
        model=raw.get("model"),
        system=system_raw,
        messages=messages,
        tools=raw.get("tools", []),
        raw=raw,
    )


def _normalize_content(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [c for c in content if isinstance(c, dict)]
    return []


def parse_usage_from_response(response_body: bytes) -> Usage | None:
    try:
        data: dict[str, Any] = json.loads(response_body)
    except json.JSONDecodeError:
        return None
    return _extract_usage(data)


def parse_usage_from_stream_frames(frames: list[dict[str, Any]]) -> Usage:
    for frame in reversed(frames):
        if frame.get("type") in ("message_delta", "message_stop"):
            usage_data = frame.get("usage", {})
            if usage_data:
                return Usage(
                    input_tokens=usage_data.get("input_tokens", 0),
                    output_tokens=usage_data.get("output_tokens", 0),
                    cache_read_input_tokens=usage_data.get("cache_read_input_tokens", 0),
                    cache_creation_input_tokens=usage_data.get("cache_creation_input_tokens", 0),
                    usage_source=UsageSource.reported,
                )
    # stream ended without usage frame
    return Usage(usage_source=UsageSource.partial, estimate_confidence=EstimateConfidence.low)


def _extract_usage(data: dict[str, Any]) -> Usage | None:
    usage_data = data.get("usage")
    if not usage_data:
        return None
    return Usage(
        input_tokens=usage_data.get("input_tokens", 0),
        output_tokens=usage_data.get("output_tokens", 0),
        cache_read_input_tokens=usage_data.get("cache_read_input_tokens", 0),
        cache_creation_input_tokens=usage_data.get("cache_creation_input_tokens", 0),
        usage_source=UsageSource.reported,
    )
