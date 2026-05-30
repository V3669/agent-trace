from __future__ import annotations

import tomllib
from pathlib import Path

from agenttrace.models import AgentId


def _load_markers() -> list[dict[str, str]]:
    markers_path = Path(__file__).parent / "markers.toml"
    with open(markers_path, "rb") as f:
        data = tomllib.load(f)
    result: list[dict[str, str]] = data.get("agent", [])
    return result


_MARKERS = _load_markers()


def detect_agent(user_agent: str) -> AgentId:
    ua_lower = user_agent.lower()
    for marker in _MARKERS:
        if "ua_prefix" in marker and ua_lower.startswith(marker["ua_prefix"].lower()):
            return AgentId(marker["id"])
        if "ua_contains" in marker and marker["ua_contains"].lower() in ua_lower:
            return AgentId(marker["id"])
    return AgentId.unknown
