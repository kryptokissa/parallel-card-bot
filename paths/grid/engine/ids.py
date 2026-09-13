"""Client order ids the exchange will actually accept.

Hyperliquid's `cloid` is a 16-byte value, and the SDK enforces it:
`hyperliquid.utils.types.Cloid._validate` raises unless the string is `0x`
followed by exactly 32 hex characters. Nothing between here and there checks —
the MCP tool passes `cloid` straight through into the order payload as field
`c` — so a readable tag like `grid-3-buy` is accepted by every layer of Python
and then rejected by the venue, on the first real order.

Reconciliation still needs to recognise its own orders, which a random id cannot
do. So ids are *derived*: a stable digest of the market and the rung, giving the
same 16 bytes for the same rung on every run, in a form the venue accepts.

`known_cloids` inverts the derivation for a bounded range of rungs, which is
what lets reconciliation tell three cases apart: a current rung, one of this
grid's rungs from a previous range, and somebody else's order that must be left
alone.
"""

from __future__ import annotations

import hashlib

NAMESPACE = "wayfinder-grid-v1"
CLOID_BYTES = 16
FLATTEN_TAG = "flatten"


def cloid_for(market: str, tag: str) -> str:
    """A venue-valid client order id for `tag` on `market`, stable across runs."""
    payload = f"{NAMESPACE}|{market}|{tag}".encode()
    digest = hashlib.blake2b(payload, digest_size=CLOID_BYTES).hexdigest()
    return f"0x{digest}"


def rung_tag(index: int, side: str) -> str:
    """The human-readable name of a rung, used in logs and reasons."""
    return f"rung-{index}-{side}"


def rung_cloid(market: str, index: int, side: str) -> str:
    return cloid_for(market, rung_tag(index, side))


def flatten_cloid(market: str) -> str:
    return cloid_for(market, FLATTEN_TAG)


def known_cloids(market: str, max_levels: int) -> dict[str, str]:
    """Every cloid this grid could have used on `market`, mapped to its tag.

    Covers rung indices below `max_levels` on both sides plus the flatten order,
    so an order left over from a previous range is still recognised as ours after
    a re-centre changed the level count.
    """
    table = {flatten_cloid(market): FLATTEN_TAG}
    for index in range(max_levels):
        for side in ("buy", "sell"):
            tag = rung_tag(index, side)
            table[cloid_for(market, tag)] = tag
    return table


def is_valid_cloid(value: object) -> bool:
    """Whether the venue would accept `value` as a cloid."""
    if not isinstance(value, str) or not value.startswith("0x"):
        return False
    body = value[2:]
    if len(body) != CLOID_BYTES * 2:
        return False
    try:
        int(body, 16)
    except ValueError:
        return False
    return True
