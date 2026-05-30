from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

_DEFAULTS_PATH = Path(__file__).parent / "defaults.toml"


@dataclass(frozen=True)
class ModelPrice:
    id: str
    provider: str
    price_input: float
    price_output: float
    price_cache_read: float
    price_cache_write: float
    as_of: str


_price_table: dict[str, ModelPrice] | None = None


def load_prices(path: Path | None = None) -> dict[str, ModelPrice]:
    global _price_table
    target = path or _DEFAULTS_PATH
    with open(target, "rb") as f:
        data = tomllib.load(f)
    table: dict[str, ModelPrice] = {}
    for entry in data.get("model", []):
        try:
            mp = ModelPrice(
                id=entry["id"],
                provider=entry["provider"],
                price_input=float(entry["price_input"]),
                price_output=float(entry["price_output"]),
                price_cache_read=float(entry.get("price_cache_read", 0.0)),
                price_cache_write=float(entry.get("price_cache_write", 0.0)),
                as_of=entry.get("as_of", "unknown"),
            )
            table[mp.id] = mp
        except (KeyError, ValueError):
            logger.warning("pricing.invalid_entry", entry=entry)
    _price_table = table
    return table


def get_price(model_id: str, path: Path | None = None) -> ModelPrice | None:
    global _price_table
    if _price_table is None:
        load_prices(path)
    assert _price_table is not None
    return _price_table.get(model_id)
