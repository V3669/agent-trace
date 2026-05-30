from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import structlog
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

from agenttrace.adapters.agents.claude_code import (
    extract_file_payloads,
    normalize_volatile,
)
from agenttrace.adapters.agents.detection import detect_agent
from agenttrace.adapters.providers import anthropic as anthropic_adapter
from agenttrace.adapters.providers.detection import detect_provider
from agenttrace.analysis.hashing import canonical_hash, sha256_hex
from agenttrace.capture.channel import enqueue
from agenttrace.config import settings
from agenttrace.models import AgentId, CaptureEvent, Provider, Usage

logger = structlog.get_logger(__name__)

# Hop-by-hop headers to strip before forwarding (RFC 2616 §13.5.1)
_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)

_UPSTREAM_MAP = {
    Provider.anthropic: settings.upstream_anthropic,
    Provider.openai: settings.upstream_openai,
}


def _build_upstream_headers(request_headers: Headers) -> dict[str, str]:
    result: dict[str, str] = {}
    for name, value in request_headers.items():
        name_l = name.lower()
        if name_l in _HOP_BY_HOP:
            continue
        if name_l == "host":
            continue
        result[name] = value
    return result


async def _tee_stream(
    source: AsyncIterator[bytes],
    capture_buf: list[bytes],
) -> AsyncIterator[bytes]:
    async for chunk in source:
        capture_buf.append(chunk)
        yield chunk


def _parse_sse_frames(raw_bytes: bytes) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    for line in raw_bytes.decode("utf-8", errors="replace").splitlines():
        if line.startswith("data: "):
            payload = line[6:].strip()
            if payload and payload != "[DONE]":
                with contextlib.suppress(json.JSONDecodeError):
                    frames.append(json.loads(payload))
    return frames


def _compute_message_hashes(canonical: Any) -> str:
    hashes = [
        sha256_hex(
            json.dumps(
                {"role": m.role, "content": m.content},
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
        )
        for m in canonical.messages
    ]
    return json.dumps(hashes)


async def proxy_handler(
    request: Request,
    capture_queue: asyncio.Queue[CaptureEvent],
    http_client: httpx.AsyncClient,
) -> Response:
    recv_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    conn_id = str(uuid.uuid4())

    headers_dict = dict(request.headers)
    provider = detect_provider(request.url.path, headers_dict)
    user_agent = headers_dict.get("user-agent", "")
    agent_id = detect_agent(user_agent)

    upstream_base = _UPSTREAM_MAP.get(provider, settings.upstream_anthropic)
    upstream_url = upstream_base.rstrip("/") + str(request.url.path)
    if request.url.query:
        upstream_url += f"?{request.url.query}"

    upstream_headers = _build_upstream_headers(request.headers)
    body_bytes = await request.body()

    capture_buf: list[bytes] = []

    try:
        async with http_client.stream(
            method=request.method,
            url=upstream_url,
            headers=upstream_headers,
            content=body_bytes,
        ) as upstream_resp:
            resp_headers = dict(upstream_resp.headers)
            for h in list(resp_headers.keys()):
                if h.lower() in _HOP_BY_HOP:
                    del resp_headers[h]

            content_type = resp_headers.get("content-type", "")
            is_streaming = "text/event-stream" in content_type

            async def streamed_body() -> AsyncIterator[bytes]:
                async for chunk in _tee_stream(upstream_resp.aiter_raw(), capture_buf):
                    yield chunk

            response: Response
            if is_streaming:
                response = StreamingResponse(
                    streamed_body(),
                    status_code=upstream_resp.status_code,
                    headers=resp_headers,
                )
            else:
                raw_body = await upstream_resp.aread()
                capture_buf.append(raw_body)
                response = Response(
                    content=raw_body,
                    status_code=upstream_resp.status_code,
                    headers=resp_headers,
                )

    except Exception:
        logger.exception("proxy.upstream_error", conn_id=conn_id)
        return Response(content=b"Bad Gateway", status_code=502)

    # Schedule capture (fail-open: errors here must never surface to caller)
    asyncio.create_task(
        _do_capture(
            recv_ts=recv_ts,
            conn_id=conn_id,
            agent_id=agent_id,
            provider=provider,
            body_bytes=body_bytes,
            capture_buf=capture_buf,
            capture_queue=capture_queue,
            is_streaming=is_streaming,
        )
    )

    return response


async def _do_capture(
    recv_ts: str,
    conn_id: str,
    agent_id: AgentId,
    provider: Provider,
    body_bytes: bytes,
    capture_buf: list[bytes],
    capture_queue: asyncio.Queue[CaptureEvent],
    is_streaming: bool,
) -> None:
    try:
        full_response = b"".join(capture_buf)

        usage: Usage | None = None
        if provider == Provider.anthropic:
            if is_streaming:
                frames = _parse_sse_frames(full_response)
                usage = anthropic_adapter.parse_usage_from_stream_frames(frames)
            else:
                usage = anthropic_adapter.parse_usage_from_response(full_response)

        canonical = (
            anthropic_adapter.normalize_request(body_bytes)
            if provider == Provider.anthropic
            else None
        )
        model = canonical.model if canonical else None
        system_prompt_hash: str | None = None
        message_hashes_json: str | None = None
        tool_defs_hash: str | None = None
        file_read_events = []

        if canonical:
            if agent_id == AgentId.claude_code:
                canonical = normalize_volatile(canonical)

            system_prompt_hash = canonical_hash(canonical.system) if canonical.system else None
            message_hashes_json = _compute_message_hashes(canonical)
            tool_defs_hash = canonical_hash(canonical.tools) if canonical.tools else None

            if agent_id == AgentId.claude_code:
                file_read_events = extract_file_payloads(canonical)

        event = CaptureEvent(
            recv_ts=recv_ts,
            conn_id=conn_id,
            agent_id=agent_id,
            provider=provider,
            model=model,
            system_prompt_hash=system_prompt_hash,
            message_hashes_json=message_hashes_json,
            tool_defs_hash=tool_defs_hash,
            body_blob=body_bytes if settings.store_bodies else None,
            usage=usage,
            file_read_events=file_read_events,
        )
        enqueue(capture_queue, event)

    except Exception:
        logger.exception("capture.parse_error", conn_id=conn_id)
