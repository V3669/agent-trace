from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog

from agenttrace.config import settings

logger = structlog.get_logger(__name__)


@dataclass
class SessionState:
    session_id: str
    agent_id: str
    last_recv_ts: datetime
    last_message_hashes: list[str]
    conn_ids: set[str] = field(default_factory=set)


def _parse_ts(ts_str: str) -> datetime:
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(tz=UTC)


def _parse_hashes(hashes_json: str | None) -> list[str]:
    if not hashes_json:
        return []
    try:
        result = json.loads(hashes_json)
        return result if isinstance(result, list) else []
    except json.JSONDecodeError:
        return []


def _is_prefix(prefix: list[str], candidate: list[str]) -> bool:
    if len(prefix) > len(candidate):
        return False
    return candidate[: len(prefix)] == prefix


def _make_session_id(recv_ts: str) -> str:
    compact = recv_ts.replace("-", "").replace(":", "").replace("T", "").replace("Z", "")
    hex_suffix = uuid.uuid4().hex[:6]
    return f"s_{compact}_{hex_suffix}"


def sessionize(conn: sqlite3.Connection) -> None:
    gap_s = settings.session_gap_seconds
    rows = conn.execute(
        """
        SELECT id, agent_id, recv_ts, message_hashes_json, conn_id
        FROM requests
        WHERE id NOT IN (SELECT request_id FROM session_events)
        ORDER BY recv_ts ASC
        """
    ).fetchall()

    active: dict[str, SessionState] = {}  # agent_id → current session

    for request_id, agent_id, recv_ts_str, hashes_json, conn_id in rows:
        recv_ts = _parse_ts(recv_ts_str)
        msg_hashes = _parse_hashes(hashes_json)
        agent_key = str(agent_id)

        current = active.get(agent_key)

        if current is None:
            session_id = _make_session_id(recv_ts_str)
            _write_session_event(conn, session_id, request_id, "start", None)
            active[agent_key] = SessionState(
                session_id=session_id,
                agent_id=agent_key,
                last_recv_ts=recv_ts,
                last_message_hashes=msg_hashes,
                conn_ids={conn_id},
            )
            continue

        gap = (recv_ts - current.last_recv_ts).total_seconds()
        if gap > gap_s:
            session_id = _make_session_id(recv_ts_str)
            _write_session_event(conn, session_id, request_id, "start", None)
            active[agent_key] = SessionState(
                session_id=session_id,
                agent_id=agent_key,
                last_recv_ts=recv_ts,
                last_message_hashes=msg_hashes,
                conn_ids={conn_id},
            )
            continue

        if _is_prefix(current.last_message_hashes, msg_hashes):
            _write_session_event(conn, current.session_id, request_id, "continue", None)
            current.last_message_hashes = msg_hashes
            current.last_recv_ts = recv_ts
            current.conn_ids.add(conn_id)
            continue

        # Check compaction: prefix fails but time gap OK.
        # Heuristic: new messages array is shorter (compacted) or starts fresh.
        prefix_mismatch = (
            msg_hashes
            and current.last_message_hashes
            and (msg_hashes[0] != current.last_message_hashes[0])
        )
        if len(msg_hashes) <= len(current.last_message_hashes) or prefix_mismatch:
            _write_session_event(conn, current.session_id, request_id, "compaction", None)
            current.last_message_hashes = msg_hashes
            current.last_recv_ts = recv_ts
            continue

        # Branch or new session
        session_id = _make_session_id(recv_ts_str)
        _write_session_event(conn, session_id, request_id, "start", None)
        active[agent_key] = SessionState(
            session_id=session_id,
            agent_id=agent_key,
            last_recv_ts=recv_ts,
            last_message_hashes=msg_hashes,
            conn_ids={conn_id},
        )

    conn.commit()


def _write_session_event(
    conn: sqlite3.Connection,
    session_id: str,
    request_id: int,
    event_type: str,
    parent_session_id: str | None,
) -> None:
    _SQL = (
        "INSERT INTO session_events "
        "(session_id, request_id, event_type, parent_session_id) "
        "VALUES (?, ?, ?, ?)"
    )
    conn.execute(_SQL, (session_id, request_id, event_type, parent_session_id))
