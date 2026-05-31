from __future__ import annotations

import asyncio
import sqlite3

import structlog

from agenttrace.models import CaptureEvent

logger = structlog.get_logger(__name__)

_BATCH_SIZE = 200
_BATCH_TIMEOUT_MS = 500

_INSERT_REQUEST = """
INSERT INTO requests (
    recv_ts, conn_id, agent_id, provider, model,
    system_prompt_hash, message_hashes_json, tool_defs_hash, body_blob,
    usage_input, usage_output, cache_read_input, cache_creation_input,
    usage_source, estimate_confidence
) VALUES (
    :recv_ts, :conn_id, :agent_id, :provider, :model,
    :system_prompt_hash, :message_hashes_json, :tool_defs_hash, :body_blob,
    :usage_input, :usage_output, :cache_read_input, :cache_creation_input,
    :usage_source, :estimate_confidence
)
"""

_INSERT_FRE = """
INSERT INTO file_read_events (request_id, path, content_hash, approx_tokens)
VALUES (:request_id, :path, :content_hash, :approx_tokens)
"""

dropped_count = 0


async def writer_task(queue: asyncio.Queue[CaptureEvent], conn: sqlite3.Connection) -> None:
    global dropped_count
    batch: list[CaptureEvent] = []

    async def flush() -> None:
        if not batch:
            return
        try:
            with conn:
                for event in batch:
                    usage = event.usage
                    params = {
                        "recv_ts": event.recv_ts,
                        "conn_id": event.conn_id,
                        "agent_id": event.agent_id.value,
                        "provider": event.provider.value,
                        "model": event.model,
                        "system_prompt_hash": event.system_prompt_hash,
                        "message_hashes_json": event.message_hashes_json,
                        "tool_defs_hash": event.tool_defs_hash,
                        "body_blob": event.body_blob,
                        "usage_input": usage.input_tokens if usage else None,
                        "usage_output": usage.output_tokens if usage else None,
                        "cache_read_input": usage.cache_read_input_tokens if usage else None,
                        "cache_creation_input": (
                            usage.cache_creation_input_tokens if usage else None
                        ),
                        "usage_source": usage.usage_source.value if usage else None,
                        "estimate_confidence": (
                            usage.estimate_confidence.value
                            if usage and usage.estimate_confidence
                            else None
                        ),
                    }
                    cursor = conn.execute(_INSERT_REQUEST, params)
                    request_id = cursor.lastrowid
                    for fre in event.file_read_events:
                        conn.execute(
                            _INSERT_FRE,
                            {
                                "request_id": request_id,
                                "path": fre.path,
                                "content_hash": fre.content_hash,
                                "approx_tokens": fre.approx_tokens,
                            },
                        )
        except Exception:
            logger.exception("writer.flush_error", batch_size=len(batch))
        finally:
            batch.clear()

    timeout_s = _BATCH_TIMEOUT_MS / 1000.0
    while True:
        try:
            event = await asyncio.wait_for(queue.get(), timeout=timeout_s)
            batch.append(event)
            queue.task_done()
            if len(batch) >= _BATCH_SIZE:
                await flush()
        except TimeoutError:
            await flush()
        except asyncio.CancelledError:
            await flush()
            return
