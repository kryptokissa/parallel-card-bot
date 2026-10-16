"""Differential test: our tick arithmetic against the SDK's own.

`engine/ticks.snap_price` is a reimplementation of
`HyperliquidAdapter.get_valid_order_price`, and it has to be exact. A level one
tick off the venue's grid is not a level, it is a rejected order — the MCP tool
refuses any price the adapter would have moved.

Reimplementing rather than importing is deliberate: the path must not depend on
the SDK's adapter at runtime (§2.3 — bind to the SDK's client classes and the
shape it expects drifts a version behind). The cost of that choice is that the
copy can silently diverge, so this test pays it down by comparing the two
directly across the whole price range, for every szDecimals a perp can have.

Only the per-asset szDecimals lookup is stubbed. The arithmetic under test —
`get_valid_order_price` and `get_price_decimals` — is the SDK's real code.
"""

from __future__ import annotations

import random

import pytest

from engine.ticks import price_decimals, snap_price

adapter_module = pytest.importorskip(
    "wayfinder_paths.adapters.hyperliquid_adapter.adapter",
    reason="wayfinder-paths is a development dependency, not a runtime one",
)

PERP_SZ_DECIMALS = (0, 1, 2, 3, 4, 5, 6)


class _Probe(adapter_module.HyperliquidAdapter):
    """The real adapter with only the metadata lookup replaced."""

    def __init__(self, sz_decimals: int) -> None:
        super().__init__()
        self._sz_decimals = sz_decimals

    def get_sz_decimals(self, asset_id: int) -> int:
        return self._sz_decimals


@pytest.mark.parametrize("sz_decimals", PERP_SZ_DECIMALS)
def test_price_decimals_matches_the_adapter(sz_decimals):
    probe = _Probe(sz_decimals)
    assert price_decimals(sz_decimals) == probe.get_price_decimals(0)


@pytest.mark.parametrize("sz_decimals", PERP_SZ_DECIMALS)
def test_snapping_matches_the_adapter_across_the_price_range(sz_decimals):
    probe = _Probe(sz_decimals)
    rng = random.Random(7)
    for _ in range(4000):
        price = rng.choice(
            [
                rng.uniform(0.0001, 1.0),
                rng.uniform(1.0, 100.0),
                rng.uniform(100.0, 10_000.0),
                rng.uniform(10_000.0, 200_000.0),
                float(rng.randint(1, 200_000)),
            ]
        )
        assert snap_price(price, sz_decimals) == probe.get_valid_order_price(0, price)


@pytest.mark.parametrize("sz_decimals", PERP_SZ_DECIMALS)
def test_boundary_prices_match_the_adapter(sz_decimals):
    probe = _Probe(sz_decimals)
    # Powers of ten and values just under them are where the significant-figure
    # cap changes step, which is where an off-by-one in `adjusted()` would hide.
    candidates = []
    for exponent in range(-4, 6):
        base = 10.0**exponent
        candidates.extend([base, base * 0.999999, base * 1.000001, base * 9.99999])
    for price in candidates:
        if price <= 0:
            continue
        assert snap_price(price, sz_decimals) == probe.get_valid_order_price(0, price)


def test_a_snapped_price_is_a_fixed_point():
    # Snapping twice must change nothing, or a refill would drift each cycle.
    rng = random.Random(11)
    for _ in range(2000):
        price = rng.uniform(0.001, 150_000.0)
        once = snap_price(price, 4)
        if once > 0:
            assert snap_price(once, 4) == once


signing = pytest.importorskip(
    "hyperliquid.utils.signing",
    reason="hyperliquid-felix arrives with wayfinder-paths, a dev dependency",
)


@pytest.mark.parametrize("sz_decimals", PERP_SZ_DECIMALS)
def test_every_price_and_size_survives_the_wire_encoder(sz_decimals):
    """`float_to_wire` raises rather than rounding, so a float artefact is fatal.

    Hyperliquid's signing path formats every number to 8 decimals and raises
    `ValueError("float_to_wire causes rounding")` if the value does not survive
    that within 1e-12. Snapping is done in `Decimal` precisely so the floats that
    come back out are representable, and this is the test that says so — across
    price magnitudes from cents to six figures, both spacings, and level counts
    up to the cap.
    """
    from engine.config import GridConfig
    from engine.levels import GridGeometryError, build_levels

    rng = random.Random(3)
    checked = 0
    for _ in range(250):
        mark = rng.choice(
            [
                rng.uniform(0.01, 1.0),
                rng.uniform(1.0, 100.0),
                rng.uniform(100.0, 5_000.0),
                rng.uniform(5_000.0, 150_000.0),
            ]
        )
        span = rng.uniform(0.02, 0.4)
        config = GridConfig(
            market="X-USDC",
            sz_decimals=sz_decimals,
            lower=mark * (1 - span),
            upper=mark * (1 + span),
            levels=rng.randint(2, 20),
            spacing=rng.choice(["geometric", "arithmetic"]),
            capital_usd=rng.choice([500.0, 5_000.0, 250_000.0]),
            leverage=rng.choice([1.0, 2.0, 5.0]),
            breakout="halt_hold",
        )
        try:
            config.validate()
            rungs = build_levels(config, mark)
        except (ValueError, GridGeometryError):
            continue
        for rung in rungs:
            signing.float_to_wire(rung.price)
            signing.float_to_wire(rung.size)
            checked += 2
    assert checked > 0, "the fuzz produced no valid grids to check"
