from __future__ import annotations

import asyncio

import structlog

from agenttrace.models import CaptureEvent

logger = structlog.get_logger(__name__)

_dropped = 0


def get_dropped_count() -> int:
    return _dropped


def enqueue(queue: asyncio.Queue[CaptureEvent], event: CaptureEvent) -> None:
    global _dropped
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        _dropped += 1
        logger.warning("capture.dropped", total_dropped=_dropped)
