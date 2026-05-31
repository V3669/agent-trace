"""Integration: byte-exact passthrough + fail-open under injected capture error."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import respx
from starlette.testclient import TestClient  # type: ignore[import-untyped]

from agenttrace.capture.db import apply_migrations, open_db
from agenttrace.models import CaptureEvent

_MIGRATIONS_DIR = Path(__file__).parent.parent.parent / "migrations"


@pytest.fixture()
def db_and_queue(
    tmp_path: Path,
) -> Generator[tuple[sqlite3.Connection, asyncio.Queue[CaptureEvent]], None, None]:
    conn = open_db(tmp_path / "test.db")
    apply_migrations(conn, _MIGRATIONS_DIR)
    queue: asyncio.Queue[CaptureEvent] = asyncio.Queue(maxsize=1000)
    try:
        yield conn, queue
    finally:
        conn.close()


@pytest.fixture()
def client(
    db_and_queue: tuple[sqlite3.Connection, asyncio.Queue[CaptureEvent]],
) -> TestClient:
    _, queue = db_and_queue
    from agenttrace.proxy.app import create_app

    starlette_app = create_app(queue)
    return TestClient(starlette_app, raise_server_exceptions=False)


_FAKE_RESPONSE = json.dumps(
    {
        "id": "msg_01",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "Hello!"}],
        "model": "claude-sonnet-4-6",
        "usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        },
    }
).encode()

# Realistic Anthropic SSE stream with cache fields and usage in message_stop
_SSE_MSG_START = (
    'data: {"type":"message_start","message":{"id":"msg_sse","type":"message",'
    '"role":"assistant","model":"claude-sonnet-4-6","usage":{"input_tokens":15,'
    '"output_tokens":0,"cache_read_input_tokens":5,"cache_creation_input_tokens":0}}}'
)
_SSE_CB_START = (
    'data: {"type":"content_block_start","index":0,'
    '"content_block":{"type":"text","text":""}}'
)
_SSE_DELTA_1 = (
    'data: {"type":"content_block_delta","index":0,'
    '"delta":{"type":"text_delta","text":"Hello"}}'
)
_SSE_DELTA_2 = (
    'data: {"type":"content_block_delta","index":0,'
    '"delta":{"type":"text_delta","text":" world"}}'
)
_SSE_MSG_DELTA = (
    'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
    '"usage":{"output_tokens":4}}'
)
_FAKE_SSE_FRAMES = "\n".join(
    [
        _SSE_MSG_START,
        "",
        _SSE_CB_START,
        "",
        _SSE_DELTA_1,
        "",
        _SSE_DELTA_2,
        "",
        'data: {"type":"content_block_stop","index":0}',
        "",
        _SSE_MSG_DELTA,
        "",
        'data: {"type":"message_stop"}',
        "",
        "data: [DONE]",
        "",
    ]
)
_FAKE_SSE_BYTES = _FAKE_SSE_FRAMES.encode()


@respx.mock
def test_byte_exact_passthrough(client: TestClient) -> None:
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200, content=_FAKE_RESPONSE, headers={"content-type": "application/json"}
        )
    )

    request_body = json.dumps(
        {
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 10,
        }
    ).encode()

    resp = client.post(
        "/v1/messages",
        content=request_body,
        headers={
            "content-type": "application/json",
            "anthropic-version": "2023-06-01",
            "x-api-key": "test-key",
        },
    )

    assert resp.status_code == 200
    assert resp.content == _FAKE_RESPONSE


@respx.mock
def test_sse_streaming_byte_exact_passthrough(client: TestClient) -> None:
    """SSE streaming response must be forwarded byte-for-byte to the client."""
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200,
            content=_FAKE_SSE_BYTES,
            headers={"content-type": "text/event-stream"},
        )
    )

    request_body = json.dumps(
        {
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 20,
            "stream": True,
        }
    ).encode()

    resp = client.post(
        "/v1/messages",
        content=request_body,
        headers={
            "content-type": "application/json",
            "anthropic-version": "2023-06-01",
            "x-api-key": "test-key",
        },
    )

    assert resp.status_code == 200
    assert resp.content == _FAKE_SSE_BYTES


@respx.mock
def test_sse_streaming_capture_populates_queue(
    client: TestClient,
    db_and_queue: tuple[sqlite3.Connection, asyncio.Queue[CaptureEvent]],
) -> None:
    """After an SSE stream, a CaptureEvent must appear in the queue with usage."""
    _, queue = db_and_queue

    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200,
            content=_FAKE_SSE_BYTES,
            headers={"content-type": "text/event-stream"},
        )
    )

    request_body = json.dumps(
        {
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 20,
            "stream": True,
        }
    ).encode()

    client.post(
        "/v1/messages",
        content=request_body,
        headers={
            "content-type": "application/json",
            "anthropic-version": "2023-06-01",
            "x-api-key": "test-key",
        },
    )

    assert not queue.empty(), "CaptureEvent not enqueued after SSE stream"
    event: CaptureEvent = queue.get_nowait()
    assert event.usage is not None
    assert event.usage.input_tokens == 15
    assert event.usage.cache_read_input_tokens == 5


@respx.mock
def test_fail_open_on_capture_error(client: TestClient) -> None:
    """Injecting an error in the capture path must NOT affect response delivery."""
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200, content=_FAKE_RESPONSE, headers={"content-type": "application/json"}
        )
    )

    _body = json.dumps(
        {"model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": "hi"}]}
    ).encode()
    with patch("agenttrace.proxy.handler.enqueue", side_effect=RuntimeError("injected")):
        resp = client.post(
            "/v1/messages",
            content=_body,
            headers={
                "content-type": "application/json",
                "anthropic-version": "2023-06-01",
                "x-api-key": "key",
            },
        )

    assert resp.status_code == 200
    assert resp.content == _FAKE_RESPONSE


@respx.mock
def test_upstream_error_returns_502(client: TestClient) -> None:
    """If upstream is unreachable, proxy must return 502 (not crash)."""
    respx.post("https://api.anthropic.com/v1/messages").mock(
        side_effect=httpx.ConnectError("connection refused")
    )

    resp = client.post(
        "/v1/messages",
        content=b"{}",
        headers={
            "content-type": "application/json",
            "anthropic-version": "2023-06-01",
            "x-api-key": "key",
        },
    )

    assert resp.status_code == 502


@respx.mock
def test_hop_by_hop_headers_stripped(client: TestClient) -> None:
    """Transfer-encoding and connection headers must not be forwarded to client."""
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200,
            content=_FAKE_RESPONSE,
            headers={
                "content-type": "application/json",
                "transfer-encoding": "chunked",
                "connection": "keep-alive",
            },
        )
    )

    resp = client.post(
        "/v1/messages",
        content=b"{}",
        headers={
            "content-type": "application/json",
            "anthropic-version": "2023-06-01",
            "x-api-key": "key",
        },
    )

    assert resp.status_code == 200
    assert "transfer-encoding" not in resp.headers
    assert "connection" not in resp.headers
