from __future__ import annotations

import re
from typing import Any

import structlog

from agenttrace.models import CanonicalRequest, FileReadEvent, ToolEvent, ToolEventType
from agenttrace.tokens.estimator import estimate_tokens

logger = structlog.get_logger(__name__)

# Matches per-request volatile hash injected by Claude Code into system prompt.
# Format: hex string of 8+ chars alone on its own line (or after known marker).
# If 0 or >1 match: do NOT strip (log volatile_strip_ambiguous).
VOLATILE_HASH_RE = re.compile(r"(?m)^[0-9a-f]{8,}$")

_FILE_READ_TOOLS = frozenset({"read", "view"})
_GLOB_TOOLS = frozenset({"glob", "grep", "ls"})
_EDIT_TOOLS = frozenset({"edit", "multiedit", "write", "create"})


def normalize_volatile(canonical: CanonicalRequest) -> CanonicalRequest:
    system_blocks = list(canonical.system)
    stripped_blocks = []
    for block in system_blocks:
        if block.get("type") != "text":
            stripped_blocks.append(block)
            continue
        text = block.get("text", "")
        matches = VOLATILE_HASH_RE.findall(text)
        if len(matches) == 1:
            stripped_text = VOLATILE_HASH_RE.sub("", text).strip()
            stripped_blocks.append({**block, "text": stripped_text})
        else:
            if matches:
                logger.warning("volatile_strip_ambiguous", match_count=len(matches))
            stripped_blocks.append(block)

    return canonical.model_copy(update={"system": stripped_blocks})


def classify_tool_calls(canonical: CanonicalRequest) -> list[ToolEvent]:
    events: list[ToolEvent] = []
    for msg in canonical.messages:
        for block in msg.content:
            if block.get("type") != "tool_use":
                continue
            tool_name: str = block.get("name", "").lower()
            tool_use_id: str = block.get("id", "")
            input_data: dict[str, Any] = block.get("input", {})
            path = input_data.get("path") or input_data.get("file_path")

            if tool_name in _FILE_READ_TOOLS:
                event_type = ToolEventType.FILE_READ
            elif tool_name in _GLOB_TOOLS:
                event_type = ToolEventType.GLOB_SCAN
            elif tool_name in _EDIT_TOOLS:
                event_type = ToolEventType.EDIT
            else:
                event_type = ToolEventType.OTHER

            events.append(
                ToolEvent(
                    tool_use_id=tool_use_id,
                    tool_name=tool_name,
                    event_type=event_type,
                    path=str(path) if path else None,
                )
            )
    return events


def extract_file_payloads(canonical: CanonicalRequest) -> list[FileReadEvent]:
    tool_use_map: dict[str, tuple[str, str]] = {}
    for msg in canonical.messages:
        for block in msg.content:
            if block.get("type") == "tool_use":
                tool_name: str = block.get("name", "").lower()
                if tool_name in _FILE_READ_TOOLS:
                    input_data: dict[str, Any] = block.get("input", {})
                    path = input_data.get("path") or input_data.get("file_path") or ""
                    tool_use_map[block.get("id", "")] = (tool_name, str(path))

    events: list[FileReadEvent] = []
    for msg in canonical.messages:
        for block in msg.content:
            if block.get("type") != "tool_result":
                continue
            tool_use_id: str = block.get("tool_use_id", "")
            if tool_use_id not in tool_use_map:
                continue
            _, path = tool_use_map[tool_use_id]
            content_raw = block.get("content", "")
            if isinstance(content_raw, list):
                content = " ".join(c.get("text", "") for c in content_raw if isinstance(c, dict))
            else:
                content = str(content_raw)

            from agenttrace.analysis.hashing import sha256_hex

            content_hash = sha256_hex(content)
            approx_tokens = estimate_tokens(content)

            events.append(
                FileReadEvent(
                    tool_use_id=tool_use_id,
                    path=path,
                    content=content,
                    content_hash=content_hash,
                    approx_tokens=approx_tokens,
                )
            )
    return events
