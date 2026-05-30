import asyncio

from agenttrace.capture.channel import enqueue
from agenttrace.models import AgentId, CaptureEvent, Provider


def _event() -> CaptureEvent:
    return CaptureEvent(
        recv_ts="2026-01-01T00:00:00Z",
        conn_id="c1",
        agent_id=AgentId.unknown,
        provider=Provider.unknown,
    )


def test_enqueue_adds_to_queue() -> None:
    queue: asyncio.Queue[CaptureEvent] = asyncio.Queue(maxsize=10)
    enqueue(queue, _event())
    assert queue.qsize() == 1


def test_enqueue_overflow_drops_and_counts() -> None:
    import agenttrace.capture.channel as ch

    original = ch._dropped
    queue: asyncio.Queue[CaptureEvent] = asyncio.Queue(maxsize=1)
    queue.put_nowait(_event())  # fill queue
    enqueue(queue, _event())  # this should drop
    assert ch._dropped > original
    assert queue.qsize() == 1  # queue still has 1 item, not 2
