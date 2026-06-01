"""Tee-ing proxy handler — the hot path.

Forwards every request byte-for-byte to the upstream provider, relays the
response (streaming or buffered) to the calling agent, and enqueues a
reference copy of the full exchange for off-path capture.

Hot-path invariant (PLAN.md §2):
  proxy_handler does ONLY:
    read incoming bytes → open upstream stream → pipe both directions
    → enqueue a reference-copy of bytes for capture.
  Zero parsing, zero tokenization, zero DB I/O here.
  All semantic work happens in _do_capture (async task, off hot path).

Fail-open (FR-1.6, R-FAILOPEN):
  Any exception in _do_capture is caught and logged without surfacing to the
  agent.  The agent's request always completes correctly.
"""

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

from agenttrace.adapters.agents import aider as aider_adapter
from agenttrace.adapters.agents.claude_code import (
    extract_file_payloads as cc_extract_payloads,
)
from agenttrace.adapters.agents.claude_code import (
    normalize_volatile as cc_normalize_volatile,
)
from agenttrace.adapters.agents.detection import detect_agent
from agenttrace.adapters.providers import anthropic as anthropic_adapter
from agenttrace.adapters.providers import openai as openai_adapter
from agenttrace.adapters.providers.detection import detect_provider
from agenttrace.analysis.hashing import canonical_hash, sha256_hex
from agenttrace.capture.channel import enqueue
from agenttrace.config import settings
from agenttrace.models import AgentId, CanonicalRequest, CaptureEvent, Provider, Usage

logger = structlog.get_logger(__name__)

# Hop-by-hop headers to strip before forwarding (RFC 2616 §13.5.1).
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

_UPSTREAM_MAP: dict[Provider, str] = {
    Provider.anthropic: settings.upstream_anthropic,
    Provider.openai: settings.upstream_openai,
}


def _build_upstream_headers(request_headers: Headers) -> dict[str, str]:
    """Strip hop-by-hop and Host headers; preserve all others verbatim (E.2)."""
    result: dict[str, str] = {}
    for name, value in request_headers.items():
        name_l = name.lower()
        if name_l in _HOP_BY_HOP or name_l == "host":
            continue
        result[name] = value
    return result


def _strip_hop_by_hop(headers: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in _HOP_BY_HOP}


def _parse_sse_frames(raw_bytes: bytes) -> list[dict[str, Any]]:
    """Extract JSON objects from SSE data lines; skip [DONE] sentinel."""
    frames: list[dict[str, Any]] = []
    for line in raw_bytes.decode("utf-8", errors="replace").splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[6:].strip()
        if payload and payload != "[DONE]":
            with contextlib.suppress(json.JSONDecodeError):
                frames.append(json.loads(payload))
    return frames


def _compute_message_hashes(canonical: CanonicalRequest) -> str:
    """Return a JSON array of per-message SHA-256 hashes (post-normalize_volatile)."""
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
    """Forward request upstream, tee response bytes into the capture queue.

    This function must stay lean: no parsing, no DB access.
    """
    recv_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    conn_id = str(uuid.uuid4())

    headers_dict = dict(request.headers)
    provider = detect_provider(request.url.path, headers_dict)
    user_agent = headers_dict.get("user-agent", "")
    agent_id = detect_agent(user_agent)

    if provider not in _UPSTREAM_MAP:
        logger.warning("proxy.unknown_provider_fallback", provider=provider, conn_id=conn_id)
    upstream_base = _UPSTREAM_MAP.get(provider, settings.upstream_anthropic)
    upstream_url = upstream_base.rstrip("/") + str(request.url.path)
    if request.url.query:
        upstream_url += f"?{request.url.query}"

    upstream_headers = _build_upstream_headers(request.headers)
    body_bytes = await request.body()

    upstream_request = http_client.build_request(
        method=request.method,
        url=upstream_url,
        headers=upstream_headers,
        content=body_bytes,
    )

    try:
        upstream_resp = await http_client.send(upstream_request, stream=True)
    except Exception:
        logger.exception("proxy.upstream_error", conn_id=conn_id)
        return Response(content=b"Bad Gateway", status_code=502)

    resp_headers = _strip_hop_by_hop(dict(upstream_resp.headers))
    is_streaming = "text/event-stream" in resp_headers.get("content-type", "")
    capture_buf: list[bytes] = []

    if is_streaming:
        async def _streaming_body() -> AsyncIterator[bytes]:
            try:
                async for chunk in upstream_resp.aiter_raw():
                    capture_buf.append(chunk)
                    yield chunk
            finally:
                await upstream_resp.aclose()
                asyncio.create_task(
                    _do_capture(
                        recv_ts=recv_ts,
                        conn_id=conn_id,
                        agent_id=agent_id,
                        provider=provider,
                        body_bytes=body_bytes,
                        capture_buf=capture_buf,
                        capture_queue=capture_queue,
                        is_streaming=True,
                    )
                )

        return StreamingResponse(
            _streaming_body(),
            status_code=upstream_resp.status_code,
            headers=resp_headers,
        )

    # Non-streaming path
    try:
        raw_body = await upstream_resp.aread()
        capture_buf.append(raw_body)
    finally:
        await upstream_resp.aclose()

    asyncio.create_task(
        _do_capture(
            recv_ts=recv_ts,
            conn_id=conn_id,
            agent_id=agent_id,
            provider=provider,
            body_bytes=body_bytes,
            capture_buf=capture_buf,
            capture_queue=capture_queue,
            is_streaming=False,
        )
    )

    return Response(
        content=raw_body,
        status_code=upstream_resp.status_code,
        headers=resp_headers,
    )


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
    """Off-hot-path capture: parse, normalise, enqueue.  Fail-open on any error."""
    try:
        full_response = b"".join(capture_buf)

        # --- Provider-specific usage extraction ---
        usage: Usage | None = _extract_usage(
            provider=provider,
            full_response=full_response,
            is_streaming=is_streaming,
        )

        # --- Provider-specific request normalisation ---
        canonical: CanonicalRequest | None = _normalize_request(
            provider=provider,
            body_bytes=body_bytes,
        )

        model = canonical.model if canonical else None
        system_prompt_hash: str | None = None
        message_hashes_json: str | None = None
        tool_defs_hash: str | None = None
        file_read_events = []

        if canonical is not None:
            # --- Agent-specific volatile normalisation + payload extraction ---
            canonical = _apply_agent_normalisation(canonical, agent_id)

            system_prompt_hash = canonical_hash(canonical.system) if canonical.system else None
            message_hashes_json = _compute_message_hashes(canonical)
            tool_defs_hash = canonical_hash(canonical.tools) if canonical.tools else None
            file_read_events = _extract_file_payloads(canonical, agent_id)

        enqueue(
            capture_queue,
            CaptureEvent(
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
            ),
        )

    except Exception:
        logger.exception("capture.parse_error", conn_id=conn_id)


# ---------------------------------------------------------------------------
# Provider / agent dispatch helpers — closed-for-modification via dict routing
# ---------------------------------------------------------------------------


def _extract_usage(
    *,
    provider: Provider,
    full_response: bytes,
    is_streaming: bool,
) -> Usage | None:
    """Dispatch to the correct provider's usage parser."""
    if provider == Provider.anthropic:
        if is_streaming:
            frames = _parse_sse_frames(full_response)
            return anthropic_adapter.parse_usage_from_stream_frames(frames)
        return anthropic_adapter.parse_usage_from_response(full_response)

    if provider == Provider.openai:
        if is_streaming:
            frames = _parse_sse_frames(full_response)
            return openai_adapter.parse_usage_from_stream_frames(frames)
        return openai_adapter.parse_usage_from_response(full_response)

    return None


def _normalize_request(
    *,
    provider: Provider,
    body_bytes: bytes,
) -> CanonicalRequest | None:
    """Dispatch to the correct provider's request normaliser."""
    if provider == Provider.anthropic:
        return anthropic_adapter.normalize_request(body_bytes)
    if provider == Provider.openai:
        return openai_adapter.normalize_request(body_bytes)
    return None


def _apply_agent_normalisation(
    canonical: CanonicalRequest,
    agent_id: AgentId,
) -> CanonicalRequest:
    """Apply agent-specific volatile stripping (E.4)."""
    if agent_id == AgentId.claude_code:
        return cc_normalize_volatile(canonical)
    if agent_id == AgentId.aider:
        return aider_adapter.normalize_volatile(canonical)
    return canonical


def _extract_file_payloads(
    canonical: CanonicalRequest,
    agent_id: AgentId,
) -> list[Any]:
    """Extract file read events using the agent-specific adapter."""
    if agent_id == AgentId.claude_code:
        return cc_extract_payloads(canonical)
    if agent_id == AgentId.aider:
        return aider_adapter.extract_file_payloads(canonical)
    return []
