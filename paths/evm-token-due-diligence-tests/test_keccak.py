"""Keccak-256 is the foundation for checksums, selectors, slots and PoolIds.

If it is wrong, every derived constant in the pack is quietly wrong, so it
is pinned to published vectors and to the boundary cases of the sponge.
"""

from evm_dd.keccak import event_topic, keccak256, keccak_hex, selector
from evm_dd.proxy import (
    SLOT_EIP1822_IMPLEMENTATION,
    SLOT_EIP1967_ADMIN,
    SLOT_EIP1967_BEACON,
    SLOT_EIP1967_IMPLEMENTATION,
)


def test_published_digest_vectors():
    assert keccak256(b"").hex() == (
        "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    )
    assert keccak256(b"abc").hex() == (
        "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45"
    )
    assert keccak256(b"testing").hex() == (
        "5f16f4c7f149ac4f9510d9cf8cf384038ad348b3bcdc01915f95de12df9d1b02"
    )


def test_rate_boundaries_are_padded_correctly():
    # 135 exercises the single-byte 0x81 pad; 136 and 272 the full blocks.
    digests = {len(b"a" * n): keccak_hex(b"a" * n) for n in (134, 135, 136, 137, 272)}
    assert len(set(digests.values())) == 5
    assert all(value.startswith("0x") and len(value) == 66 for value in digests.values())


def test_function_selectors_match_published_values():
    assert selector("transfer(address,uint256)") == "0xa9059cbb"
    assert selector("balanceOf(address)") == "0x70a08231"
    assert selector("owner()") == "0x8da5cb5b"
    assert selector("transferOwnership(address)") == "0xf2fde38b"
    assert selector("upgradeTo(address)") == "0x3659cfe6"
    assert selector("upgradeToAndCall(address,bytes)") == "0x4f1ef286"
    assert selector("mint(address,uint256)") == "0x40c10f19"
    assert selector("grantRole(bytes32,address)") == "0x2f2ff15d"


def test_transfer_event_topic():
    assert event_topic("Transfer(address,address,uint256)") == (
        "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
    )


def test_standard_proxy_slots_derive_to_their_specified_values():
    assert SLOT_EIP1967_IMPLEMENTATION == (
        "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
    )
    assert SLOT_EIP1967_ADMIN == (
        "0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103"
    )
    assert SLOT_EIP1967_BEACON == (
        "0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50"
    )
    assert SLOT_EIP1822_IMPLEMENTATION == (
        "0xc5f16f0fcc639fa48a6947836d9850f504798523bf8c9a3a87d5876cf622bcf7"
    )
