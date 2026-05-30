from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
import structlog
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from agenttrace.models import CaptureEvent
from agenttrace.proxy.handler import proxy_handler

logger = structlog.get_logger(__name__)

_CLIENT_TIMEOUTS = httpx.Timeout(connect=10.0, read=None, write=30.0, pool=10.0)


def create_app(
    capture_queue: asyncio.Queue[CaptureEvent],
) -> Starlette:
    http_client = httpx.AsyncClient(
        timeout=_CLIENT_TIMEOUTS,
        follow_redirects=False,
        http2=True,
    )

    async def handle_all(request: Request) -> Response:
        return await proxy_handler(request, capture_queue, http_client)

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncGenerator[None, None]:
        logger.info("proxy.started")
        yield
        await http_client.aclose()
        logger.info("proxy.stopped")

    _methods = ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"]
    routes = [Route("/{path:path}", handle_all, methods=_methods)]

    return Starlette(routes=routes, lifespan=lifespan)
