import asyncio
import sqlite3
from pathlib import Path

import pytest

from agenttrace.capture.db import apply_migrations, open_db
from agenttrace.capture.writer import writer_task
from agenttrace.models import AgentId, CaptureEvent, Provider, Usage, UsageSource

_MIGRATIONS_DIR = Path(__file__).parent.parent.parent.parent / "migrations"


@pytest.fixture()
def db_conn(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "test.db"
    conn = open_db(db_path)
    apply_migrations(conn, _MIGRATIONS_DIR)
    yield conn  # type: ignore[misc]
    conn.close()


def _make_event() -> CaptureEvent:
    return CaptureEvent(
        recv_ts="2026-01-01T00:00:00Z",
        conn_id="conn-001",
        agent_id=AgentId.claude_code,
        provider=Provider.anthropic,
        model="claude-sonnet-4-6",
        usage=Usage(input_tokens=100, output_tokens=20, usage_source=UsageSource.reported),
    )


@pytest.mark.asyncio
async def test_writer_persists_event(db_conn: sqlite3.Connection) -> None:
    queue: asyncio.Queue[CaptureEvent] = asyncio.Queue(maxsize=100)
    task = asyncio.create_task(writer_task(queue, db_conn))

    await queue.put(_make_event())
    await asyncio.sleep(0.6)  # wait past batch timeout

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    rows = db_conn.execute("SELECT conn_id, usage_input FROM requests").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "conn-001"
    assert rows[0][1] == 100


@pytest.mark.asyncio
async def test_writer_batch_multiple(db_conn: sqlite3.Connection) -> None:
    queue: asyncio.Queue[CaptureEvent] = asyncio.Queue(maxsize=1000)
    task = asyncio.create_task(writer_task(queue, db_conn))

    for _ in range(5):
        await queue.put(_make_event())
    await asyncio.sleep(0.6)

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    count = db_conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
    assert count == 5
