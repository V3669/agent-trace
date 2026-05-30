from __future__ import annotations

from agenttrace.models import Provider


def detect_provider(path: str, headers: dict[str, str]) -> Provider:
    path_l = path.lower()
    headers_l = {k.lower(): v for k, v in headers.items()}

    if "/v1/messages" in path_l or "anthropic-version" in headers_l:
        return Provider.anthropic
    if "/v1/chat/completions" in path_l or "/v1/responses" in path_l:
        return Provider.openai
    return Provider.unknown
