"""Pool identity, checked against published mainnet deployments.

These four addresses are the only real onchain data in the test suite.
They are here because a CREATE2 derivation that is subtly wrong points
every later read at an unrelated contract.
"""

import pytest

from evm_dd.addresses import to_checksum
from evm_dd.pools import (
    NATIVE_CURRENCY,
    PoolDiscovery,
    PoolError,
    PoolKeyV4,
    V3Position,
    sort_currencies,
    v2_pair_address,
    v3_pool_address,
)

UNISWAP_V3_FACTORY = "0x1F98431c8aD98523631AE4a59f267346ea31F984"
UNISWAP_V2_FACTORY = "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f"
USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
WETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"


@pytest.mark.parametrize(
    "fee,expected",
    [
        (500, "0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640"),
        (3000, "0x8ad599c3A0ff1De082011EFDDc58f1908eb6e6D8"),
    ],
)
def test_v3_pool_derivation_matches_the_deployed_pools(fee, expected):
    assert to_checksum(v3_pool_address(UNISWAP_V3_FACTORY, USDC, WETH, fee)) == expected


def test_v2_pair_derivation_matches_the_deployed_pair():
    assert to_checksum(v2_pair_address(UNISWAP_V2_FACTORY, USDC, WETH)) == (
        "0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc"
    )


def test_fee_tiers_are_different_pools_for_the_same_pair():
    assert v3_pool_address(UNISWAP_V3_FACTORY, USDC, WETH, 500) != v3_pool_address(
        UNISWAP_V3_FACTORY, USDC, WETH, 3000
    )


def test_currency_ordering_is_part_of_v4_identity():
    low, high = sort_currencies(WETH, USDC)
    key = PoolKeyV4(currency0=low, currency1=high, fee=3000, tick_spacing=60)
    assert key.pool_id.startswith("0x") and len(key.pool_id) == 66
    with pytest.raises(PoolError):
        PoolKeyV4(currency0=high, currency1=low, fee=3000, tick_spacing=60)


def test_v4_pool_id_changes_with_every_component_of_the_key():
    base = PoolKeyV4(currency0=NATIVE_CURRENCY, currency1=USDC, fee=3000, tick_spacing=60)
    variants = [
        PoolKeyV4(currency0=NATIVE_CURRENCY, currency1=USDC, fee=500, tick_spacing=60),
        PoolKeyV4(currency0=NATIVE_CURRENCY, currency1=USDC, fee=3000, tick_spacing=10),
        PoolKeyV4(
            currency0=NATIVE_CURRENCY,
            currency1=USDC,
            fee=3000,
            tick_spacing=60,
            hooks="0x00000000000000000000000000000000000000aa",
        ),
        PoolKeyV4(currency0=USDC, currency1=WETH, fee=3000, tick_spacing=60),
    ]
    assert len({base.pool_id, *[item.pool_id for item in variants]}) == 5


def test_the_v4_singleton_warning_always_travels_with_the_key():
    key = PoolKeyV4(currency0=NATIVE_CURRENCY, currency1=USDC, fee=3000, tick_spacing=60)
    assert any("singleton" in warning for warning in key.warnings())
    hooked = PoolKeyV4(
        currency0=NATIVE_CURRENCY,
        currency1=USDC,
        fee=3000,
        tick_spacing=60,
        hooks="0x00000000000000000000000000000000000000aa",
    )
    assert hooked.has_hook
    assert any("hook" in warning for warning in hooked.warnings())


def test_a_position_lists_every_way_principal_can_leave():
    position = V3Position(
        position_manager="0x" + "99" * 20,
        token_id=7,
        token0=USDC,
        token1=WETH,
        fee=3000,
        tick_lower=-887220,
        tick_upper=887220,
        liquidity=10**18,
        owner="0x" + "aa" * 20,
        operator="0x" + "bb" * 20,
        approved_for_all=["0x" + "cc" * 20],
    )
    paths = position.removal_paths()
    assert position.is_full_range and position.holds_principal
    assert any("decreaseLiquidity" in item for item in paths)
    assert any("operator" in item for item in paths)
    assert any("approvedForAll" in item for item in paths)


def test_discovery_without_declared_sources_is_never_exhaustive():
    discovery = PoolDiscovery()
    assert not discovery.is_exhaustive
    assert "not ruled out" in discovery.coverage_sentence()
    discovery.sources.append("factory event scan")
    discovery.add_limit("archive range unavailable before block 19,000,000")
    assert not discovery.is_exhaustive
    assert "archive range" in discovery.coverage_sentence()
