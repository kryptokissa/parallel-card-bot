"""Client order ids, checked against the validator that actually rejects them.

Hyperliquid's cloid is a 16-byte value. Nothing between the path and the venue
enforces that — the MCP tool passes `cloid` straight into the order payload — so
a readable tag survives every layer of Python and fails on the first real order.
The authority is `hyperliquid.utils.types.Cloid`, so these tests use it directly
rather than restating its rule.
"""

from __future__ import annotations

import pytest

from engine.config import MAX_LEVELS
from engine.ids import (
    FLATTEN_TAG,
    cloid_for,
    flatten_cloid,
    is_valid_cloid,
    known_cloids,
    rung_cloid,
    rung_tag,
)

hl_types = pytest.importorskip(
    "hyperliquid.utils.types",
    reason="hyperliquid-felix arrives with wayfinder-paths, a dev dependency",
)


def test_the_venue_validator_accepts_a_derived_rung_cloid():
    cloid = rung_cloid("BTC-USDC", 3, "buy")
    assert hl_types.Cloid(cloid).to_raw() == cloid


def test_the_venue_validator_accepts_every_cloid_the_grid_can_produce():
    for market in ("BTC-USDC", "ETH-USDC", "xyz:SP500"):
        for cloid in known_cloids(market, MAX_LEVELS):
            hl_types.Cloid(cloid)


def test_the_venue_validator_rejects_a_readable_tag():
    # This is the bug these ids exist to avoid.
    with pytest.raises(TypeError):
        hl_types.Cloid(rung_tag(3, "buy"))


def test_anything_we_accept_the_sdk_accepts():
    """Our check must never be looser than the SDK's, only tighter."""
    candidates = [
        rung_cloid("BTC-USDC", 0, "buy"),
        flatten_cloid("BTC-USDC"),
        "rung-0-buy",
        "0x",
        "0x" + "a" * 31,
        "0x" + "a" * 33,
        "0x" + "z" * 32,
        "a" * 34,
        "",
    ]
    for candidate in candidates:
        if is_valid_cloid(candidate):
            hl_types.Cloid(candidate)


def test_we_are_stricter_than_the_sdk_about_hex():
    # `Cloid._validate` checks the 0x prefix and the length, and nothing else, so
    # it hands 32 non-hex characters straight to the exchange. Being stricter
    # here means the rejection happens locally instead of on a live order.
    not_hex = "0x" + "z" * 32
    hl_types.Cloid(not_hex)  # the SDK is happy with this
    assert not is_valid_cloid(not_hex)


def test_everything_the_sdk_rejects_we_reject_too():
    for candidate in ("rung-0-buy", "0x", "0x" + "a" * 31, "a" * 34, ""):
        try:
            hl_types.Cloid(candidate)
        except (TypeError, IndexError):
            assert not is_valid_cloid(candidate), candidate


def test_non_strings_are_not_cloids():
    assert not is_valid_cloid(None)
    assert not is_valid_cloid(12345)


def test_derivation_is_stable_across_calls():
    assert rung_cloid("BTC-USDC", 7, "sell") == rung_cloid("BTC-USDC", 7, "sell")
    assert flatten_cloid("BTC-USDC") == flatten_cloid("BTC-USDC")


def test_derivation_separates_market_index_and_side():
    assert rung_cloid("BTC-USDC", 3, "buy") != rung_cloid("ETH-USDC", 3, "buy")
    assert rung_cloid("BTC-USDC", 3, "buy") != rung_cloid("BTC-USDC", 4, "buy")
    assert rung_cloid("BTC-USDC", 3, "buy") != rung_cloid("BTC-USDC", 3, "sell")
    assert flatten_cloid("BTC-USDC") != rung_cloid("BTC-USDC", 0, "buy")


def test_known_cloids_covers_both_sides_and_the_flatten_order():
    table = known_cloids("BTC-USDC", MAX_LEVELS)
    assert len(table) == MAX_LEVELS * 2 + 1
    assert table[flatten_cloid("BTC-USDC")] == FLATTEN_TAG
    assert table[rung_cloid("BTC-USDC", 0, "buy")] == rung_tag(0, "buy")


def test_known_cloids_is_market_scoped():
    # Another market's orders must not be mistaken for this grid's.
    btc = set(known_cloids("BTC-USDC", MAX_LEVELS))
    eth = set(known_cloids("ETH-USDC", MAX_LEVELS))
    assert not btc & eth


def test_known_cloids_still_recognises_rungs_from_a_larger_previous_grid():
    # A re-centre can shrink the level count; the leftover orders are still ours.
    table = known_cloids("BTC-USDC", MAX_LEVELS)
    assert rung_cloid("BTC-USDC", MAX_LEVELS - 1, "sell") in table


def test_the_namespace_is_part_of_the_derivation():
    assert cloid_for("BTC-USDC", "rung-0-buy") == rung_cloid("BTC-USDC", 0, "buy")
    assert cloid_for("BTC-USDC", "something-else") != rung_cloid(
        "BTC-USDC", 0, "buy"
    )
