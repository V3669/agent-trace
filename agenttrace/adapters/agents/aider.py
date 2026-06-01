"""Aider agent adapter.

Classifies tool calls and extracts file payloads from Aider sessions.
Aider uses the OpenAI-format API; tool names and argument schemas differ
from Claude Code.  File reads appear as OpenAI tool_calls in assistant
messages and as tool-role messages carrying the result.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from agenttrace.analysis.hashing import sha256_hex
from agenttrace.models import CanonicalRequest, FileReadEvent, ToolEvent, ToolEventType
from agenttrace.tokens.estimator import estimate_tokens

logger = structlog.get_logger(__name__)

# Aider tool-name sets (lowercased).
_FILE_READ_TOOLS = frozenset({"read_file", "view_file", "read", "view", "cat", "get_file_content"})
_GLOB_TOOLS = frozenset({"list_dir", "find_files", "glob", "grep", "ls", "search_files"})
_EDIT_TOOLS = frozenset(
    {
        "write_file",
        "create_file",
        "str_replace",
        "edit",
        "write",
        "replace_in_file",
        "apply_diff",
    }
)


def normalize_volatile(canonical: CanonicalRequest) -> CanonicalRequest:
    """Aider does not inject a per-request volatile hash.  Return unchanged."""
    return canonical


def classify_tool_calls(canonical: CanonicalRequest) -> list[ToolEvent]:
    """Identify FILE_READ / GLOB_SCAN / EDIT / OTHER tool events from Aider messages."""
    events: list[ToolEvent] = []
    for msg in canonical.messages:
        for block in msg.content:
            if block.get("type") == "tool_calls":
                for tc in block.get("tool_calls", []):
                    events.extend(_classify_openai_tool_call(tc))
    return events


def extract_file_payloads(canonical: CanonicalRequest) -> list[FileReadEvent]:
    """Correlate FILE_READ tool_calls with their tool-result payloads.

    Scans assistant messages for tool_calls whose function name is a
    FILE_READ tool, builds a map from tool_call_id → path, then scans
    tool-role messages for matching results and emits FileReadEvents.
    """
    # Phase 1: collect tool_call_id → path for FILE_READ calls.
    tool_use_map: dict[str, str] = {}
    for msg in canonical.messages:
        if msg.role != "assistant":
            continue
        for block in msg.content:
            if block.get("type") != "tool_calls":
                continue
            for tc in block.get("tool_calls", []):
                func = tc.get("function", {})
                tool_name = func.get("name", "").lower()
                if tool_name not in _FILE_READ_TOOLS:
                    continue
                args = _safe_parse_args(func.get("arguments", "{}"))
                path = args.get("path") or args.get("file_path") or args.get("filename") or ""
                tool_use_map[tc.get("id", "")] = str(path)

    if not tool_use_map:
        return []

    # Phase 2: find matching tool results.
    events: list[FileReadEvent] = []
    for msg in canonical.messages:
        if msg.role != "tool":
            continue
        for block in msg.content:
            if block.get("type") != "tool_result_openai":
                continue
            tool_call_id = block.get("tool_call_id", "")
            if tool_call_id not in tool_use_map:
                continue
            path = tool_use_map[tool_call_id]
            content_raw = block.get("content", "")
            content = _flatten_content(content_raw)
            events.append(
                FileReadEvent(
                    tool_use_id=tool_call_id,
                    path=path,
                    content=content,
                    content_hash=sha256_hex(content),
                    approx_tokens=estimate_tokens(content),
                )
            )
    return events


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _classify_openai_tool_call(tc: dict[str, Any]) -> list[ToolEvent]:
    func = tc.get("function", {})
    tool_name = func.get("name", "").lower()
    tool_call_id = tc.get("id", "")
    args = _safe_parse_args(func.get("arguments", "{}"))
    path = args.get("path") or args.get("file_path") or args.get("filename")

    if tool_name in _FILE_READ_TOOLS:
        event_type = ToolEventType.FILE_READ
    elif tool_name in _GLOB_TOOLS:
        event_type = ToolEventType.GLOB_SCAN
    elif tool_name in _EDIT_TOOLS:
        event_type = ToolEventType.EDIT
    else:
        event_type = ToolEventType.OTHER

    return [
        ToolEvent(
            tool_use_id=tool_call_id,
            tool_name=tool_name,
            event_type=event_type,
            path=str(path) if path else None,
        )
    ]


def _safe_parse_args(arguments: Any) -> dict[str, Any]:
    """Parse tool function arguments safely; return empty dict on failure."""
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _flatten_content(content: Any) -> str:
    """Flatten OpenAI tool result content to a plain string."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(block.get("text", "") for block in content if isinstance(block, dict))
    return ""
