from pathlib import Path

from agenttrace.pricing.loader import ModelPrice, get_price, load_prices

_DEFAULTS = Path(__file__).parent.parent.parent.parent / "agenttrace" / "pricing" / "defaults.toml"


def test_load_defaults_returns_models() -> None:
    prices = load_prices(_DEFAULTS)
    assert len(prices) > 0
    assert all(isinstance(v, ModelPrice) for v in prices.values())


def test_get_price_known_model() -> None:
    load_prices(_DEFAULTS)
    price = get_price("claude-sonnet-4-6", _DEFAULTS)
    assert price is not None
    assert price.price_input > 0
    assert price.price_cache_read < price.price_input


def test_get_price_unknown_model() -> None:
    price = get_price("nonexistent-model-xyz")
    assert price is None


def test_load_custom_table(tmp_path: Path) -> None:
    import agenttrace.pricing.loader as loader_mod

    custom = tmp_path / "prices.toml"
    _toml = (
        '[[model]]\nid = "my-model"\nprovider = "test"\n'
        "price_input = 0.001\nprice_output = 0.002\n"
        'price_cache_read = 0.0001\nprice_cache_write = 0.00125\nas_of = "2026-01-01"\n'
    )
    custom.write_text(_toml, encoding="utf-8")
    prices = load_prices(custom)
    assert "my-model" in prices
    assert prices["my-model"].price_input == 0.001
    # Restore global table to defaults so other tests aren't affected
    loader_mod._price_table = None
