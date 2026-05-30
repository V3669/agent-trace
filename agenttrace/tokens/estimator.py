from __future__ import annotations

from typing import Union

import structlog

logger = structlog.get_logger(__name__)

_encoder: Union["object", None] = None  # tiktoken.Encoding | False | None
_encoder_initialized = False


def _get_encoder() -> object:
    global _encoder, _encoder_initialized
    if not _encoder_initialized:
        _encoder_initialized = True
        try:
            import tiktoken

            _encoder = tiktoken.get_encoding("cl100k_base")
        except Exception:
            logger.warning("tiktoken.unavailable")
            _encoder = None
    return _encoder


def estimate_tokens(text: str) -> int:
    enc = _get_encoder()
    if enc is None:
        # Rough fallback: ~4 chars per token
        return max(1, len(text) // 4)
    try:
        import tiktoken

        assert isinstance(enc, tiktoken.Encoding)
        return len(enc.encode(text))
    except Exception:
        return max(1, len(text) // 4)
