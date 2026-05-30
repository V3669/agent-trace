from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Provider(StrEnum):
    anthropic = "anthropic"
    openai = "openai"
    unknown = "unknown"


class AgentId(StrEnum):
    claude_code = "claude_code"
    aider = "aider"
    unknown = "unknown"


class UsageSource(StrEnum):
    reported = "reported"
    partial = "partial"
    estimated = "estimated"


class EstimateConfidence(StrEnum):
    low = "low"


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    usage_source: UsageSource = UsageSource.reported
    estimate_confidence: EstimateConfidence | None = None


class CanonicalMessage(BaseModel):
    role: str
    content: list[dict[str, Any]]


class CanonicalRequest(BaseModel):
    model: str | None = None
    system: list[dict[str, Any]] = Field(default_factory=list)
    messages: list[CanonicalMessage] = Field(default_factory=list)
    tools: list[dict[str, Any]] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class ToolEventType(StrEnum):
    FILE_READ = "FILE_READ"
    GLOB_SCAN = "GLOB_SCAN"
    EDIT = "EDIT"
    OTHER = "OTHER"


class ToolEvent(BaseModel):
    tool_use_id: str
    tool_name: str
    event_type: ToolEventType
    path: str | None = None


class FileReadEvent(BaseModel):
    request_id: int | None = None
    tool_use_id: str
    path: str
    content: str
    content_hash: str
    approx_tokens: int = 0


class CaptureEvent(BaseModel):
    recv_ts: str
    conn_id: str
    agent_id: AgentId
    provider: Provider
    model: str | None = None
    system_prompt_hash: str | None = None
    message_hashes_json: str | None = None
    tool_defs_hash: str | None = None
    body_blob: bytes | None = None
    usage: Usage | None = None
    file_read_events: list[FileReadEvent] = Field(default_factory=list)
    request_headers: dict[str, str] = Field(default_factory=dict)


class SessionEventType(StrEnum):
    start = "start"
    continue_ = "continue"
    compaction = "compaction"
    branch = "branch"


class WasteReport(BaseModel):
    session_id: str
    total_billed_input_cost: float
    wasted_cost: float
    avoidable_pct: float
    fixed_overhead_cost: float
    total_requests: int
    wasted_requests: int
