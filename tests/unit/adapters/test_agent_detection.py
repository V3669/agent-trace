from agenttrace.adapters.agents.detection import detect_agent
from agenttrace.models import AgentId


def test_detect_claude_code() -> None:
    assert detect_agent("claude-cli/1.0.0 (linux)") == AgentId.claude_code


def test_detect_claude_code_case_insensitive() -> None:
    assert detect_agent("Claude-CLI/2.0") == AgentId.claude_code


def test_detect_aider() -> None:
    assert detect_agent("aider/0.50.0") == AgentId.aider


def test_detect_unknown() -> None:
    assert detect_agent("curl/7.88.0") == AgentId.unknown


def test_detect_empty_ua() -> None:
    assert detect_agent("") == AgentId.unknown
