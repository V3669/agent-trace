"""Integration: byte-exact passthrough + fail-open under injected capture error."""

from __future__ import annotations

import asyncio
import json
import sqlite3
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
def db_and_queue(tmp_path: Path) -> tuple[sqlite3.Connection, asyncio.Queue[CaptureEvent]]:
    conn = open_db(tmp_path / "test.db")
    apply_migrations(conn, _MIGRATIONS_DIR)
    queue: asyncio.Queue[CaptureEvent] = asyncio.Queue(maxsize=1000)
    return conn, queue


@pytest.fixture()
def client(db_and_queue: tuple[sqlite3.Connection, asyncio.Queue[CaptureEvent]]) -> TestClient:
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
