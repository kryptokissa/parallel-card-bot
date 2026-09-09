"""Pool identity for v2-style pairs, Uniswap v3 and Uniswap v4.

A pool must be named by an exact address or a complete pool key. "The
WETH pair" is not an identifier: forks share factories' shape but not
their init code, fee tiers multiply the pools for one token pair, and in
v4 a pool is a key inside a singleton and has no address of its own.

Two mistakes this module exists to prevent:

*   Deriving a pool address with the wrong factory or init-code hash and
    then reading a contract that has nothing to do with the target. Every
    derivation here is a *candidate* until the address is confirmed to
    hold code and to report the expected tokens.
*   Reading the v4 PoolManager's token balance and calling it a pool's
    reserves. The singleton holds every pool's currency at once.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from evm_dd.abi import encode
from evm_dd.addresses import normalize, to_checksum
from evm_dd.keccak import keccak256

NATIVE_CURRENCY = "0x0000000000000000000000000000000000000000"

# Canonical deployments. Anything not listed must be supplied explicitly and
# confirmed onchain — forks reuse names, not code.
UNISWAP_V3_INIT_CODE_HASH = (
    "0xe34f199b19b2b4f47f68442619d555527d244f78a3297ea89325f843f87b8b54"
)
UNISWAP_V2_INIT_CODE_HASH = (
    "0x96e8ac4277198ff8b6f785478aa9a39f403cb768dd02cbee326c3e7da348845f"
)


class PoolError(ValueError):
    pass


def sort_currencies(token_a: str, token_b: str) -> tuple[str, str]:
    """Uniswap ordering: ascending by address as a number."""
    first, second = normalize(token_a), normalize(token_b)
    if first == second:
        raise PoolError("a pool needs two distinct currencies")
    return (first, second) if int(first, 16) < int(second, 16) else (second, first)


def create2_address(deployer: str, salt: bytes, init_code_hash: str) -> str:
    if len(salt) != 32:
        raise PoolError(f"salt must be 32 bytes, got {len(salt)}")
    digest = keccak256(
        b"\xff"
        + bytes.fromhex(normalize(deployer)[2:])
        + salt
        + bytes.fromhex(str(init_code_hash).removeprefix("0x"))
    )
    return "0x" + digest[12:].hex()


def v2_pair_address(
    factory: str,
    token_a: str,
    token_b: str,
    init_code_hash: str = UNISWAP_V2_INIT_CODE_HASH,
) -> str:
    """Candidate v2-style pair address. Confirm against the factory getter."""
    token0, token1 = sort_currencies(token_a, token_b)
    salt = keccak256(bytes.fromhex(token0[2:]) + bytes.fromhex(token1[2:]))
    return create2_address(factory, salt, init_code_hash)


def v3_pool_address(
    factory: str,
    token_a: str,
    token_b: str,
    fee: int,
    init_code_hash: str = UNISWAP_V3_INIT_CODE_HASH,
) -> str:
    """Candidate v3 pool address. Confirm against ``factory.getPool``."""
    token0, token1 = sort_currencies(token_a, token_b)
    salt = keccak256(encode(["address", "address", "uint24"], [token0, token1, fee]))
    return create2_address(factory, salt, init_code_hash)


@dataclass(frozen=True)
class PoolKeyV4:
    """A complete Uniswap v4 pool key. The pool has no address of its own."""

    currency0: str
    currency1: str
    fee: int
    tick_spacing: int
    hooks: str = NATIVE_CURRENCY

    def __post_init__(self) -> None:
        first = normalize(self.currency0)
        second = normalize(self.currency1)
        if int(first, 16) >= int(second, 16):
            raise PoolError(
                "v4 currencies must be strictly ascending: "
                f"{to_checksum(first)} is not below {to_checksum(second)}. "
                "Ordering is part of the identity — swapping them names a "
                "different pool."
            )
        object.__setattr__(self, "currency0", first)
        object.__setattr__(self, "currency1", second)
        object.__setattr__(self, "hooks", normalize(self.hooks))
        if not 0 <= self.fee < (1 << 24):
            raise PoolError(f"fee {self.fee} is out of uint24 range")
        if not -(1 << 23) <= self.tick_spacing < (1 << 23):
            raise PoolError(f"tickSpacing {self.tick_spacing} is out of int24 range")

    @property
    def pool_id(self) -> str:
        """keccak256 of the abi-encoded key — the v4 PoolId."""
        return "0x" + keccak256(
            encode(
                ["address", "address", "uint24", "int24", "address"],
                [self.currency0, self.currency1, self.fee, self.tick_spacing, self.hooks],
            )
        ).hex()

    @property
    def has_hook(self) -> bool:
        return int(self.hooks, 16) != 0

    @property
    def is_native_pool(self) -> bool:
        return int(self.currency0, 16) == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "currency0": to_checksum(self.currency0),
            "currency1": to_checksum(self.currency1),
            "fee": self.fee,
            "tick_spacing": self.tick_spacing,
            "hooks": to_checksum(self.hooks),
            "pool_id": self.pool_id,
            "has_hook": self.has_hook,
            "native_currency0": self.is_native_pool,
        }

    def warnings(self) -> list[str]:
        notes = [
            "The v4 PoolManager is a singleton. Its balance of either "
            "currency is the sum across every pool it holds and is not this "
            "pool's reserves; read the pool's own liquidity and positions."
        ]
        if self.has_hook:
            notes.append(
                f"This pool has a hook at {to_checksum(self.hooks)}. The hook "
                "can run code on initialize, swap, donate and liquidity "
                "changes: it can take fees, block a swap, or make a position "
                "unexitable. It is part of the pool's risk surface and needs "
                "its own control review."
            )
        return notes


# ``positions(uint256)`` on the Uniswap v3 NonfungiblePositionManager.
V3_POSITION_TYPES = (
    "uint96",   # nonce
    "address",  # operator
    "address",  # token0
    "address",  # token1
    "uint24",   # fee
    "int24",    # tickLower
    "int24",    # tickUpper
    "uint128",  # liquidity
    "uint256",  # feeGrowthInside0LastX128
    "uint256",  # feeGrowthInside1LastX128
    "uint128",  # tokensOwed0
    "uint128",  # tokensOwed1
)


@dataclass
class V3Position:
    """A v3 position NFT: principal lives here, not in the pool contract."""

    position_manager: str
    token_id: int
    token0: str
    token1: str
    fee: int
    tick_lower: int
    tick_upper: int
    liquidity: int
    tokens_owed0: int = 0
    tokens_owed1: int = 0
    operator: str = NATIVE_CURRENCY
    owner: str | None = None
    approved: str | None = None
    approved_for_all: list[str] = field(default_factory=list)

    @classmethod
    def from_positions_return(
        cls, position_manager: str, token_id: int, data: str | bytes
    ) -> "V3Position":
        from evm_dd.abi import decode

        values = decode(list(V3_POSITION_TYPES), data)
        return cls(
            position_manager=normalize(position_manager),
            token_id=token_id,
            operator=values[1],
            token0=values[2],
            token1=values[3],
            fee=values[4],
            tick_lower=values[5],
            tick_upper=values[6],
            liquidity=values[7],
            tokens_owed0=values[10],
            tokens_owed1=values[11],
        )

    @property
    def is_full_range(self) -> bool:
        return self.tick_lower <= -887000 and self.tick_upper >= 887000

    @property
    def holds_principal(self) -> bool:
        return self.liquidity > 0

    def removal_paths(self) -> list[str]:
        """Every way principal can leave, given who holds what."""
        paths = [
            f"owner {to_checksum(self.owner) if self.owner else '<unresolved>'} "
            "can decreaseLiquidity + collect",
            "owner can transfer the position NFT to anyone",
        ]
        if self.operator and int(self.operator, 16) != 0:
            paths.append(
                f"operator {to_checksum(self.operator)} is approved on this "
                "position and can move or drain it"
            )
        if self.approved and int(self.approved, 16) != 0:
            paths.append(
                f"{to_checksum(self.approved)} holds an ERC-721 approval on "
                "this token id"
            )
        for operator in self.approved_for_all:
            paths.append(
                f"{to_checksum(operator)} is approvedForAll on the owner's "
                "positions, this one included"
            )
        return paths

    def to_dict(self) -> dict[str, Any]:
        return {
            "position_manager": to_checksum(self.position_manager),
            "token_id": self.token_id,
            "token0": to_checksum(self.token0),
            "token1": to_checksum(self.token1),
            "fee": self.fee,
            "tick_lower": self.tick_lower,
            "tick_upper": self.tick_upper,
            "full_range": self.is_full_range,
            "liquidity": str(self.liquidity),
            "holds_principal": self.holds_principal,
            "tokens_owed0": str(self.tokens_owed0),
            "tokens_owed1": str(self.tokens_owed1),
            "owner": to_checksum(self.owner) if self.owner else None,
            "operator": to_checksum(self.operator) if self.operator else None,
            "approved": to_checksum(self.approved) if self.approved else None,
            "approved_for_all": [to_checksum(item) for item in self.approved_for_all],
            "removal_paths": self.removal_paths(),
        }


@dataclass
class PoolDiscovery:
    """What was searched for pools, and what that search could not cover.

    A pool list without this is a claim about the whole market made from an
    unstated sample. Declaring the universe is what lets a reader tell
    "there are no other pools" from "we did not look for others".
    """

    sources: list[str] = field(default_factory=list)
    factories_searched: list[str] = field(default_factory=list)
    fee_tiers_searched: list[int] = field(default_factory=list)
    quote_assets_searched: list[str] = field(default_factory=list)
    from_block: int | None = None
    to_block: int | None = None
    limits: list[str] = field(default_factory=list)
    pools: list[dict[str, Any]] = field(default_factory=list)

    def add_limit(self, text: str) -> None:
        self.limits.append(text)

    @property
    def is_exhaustive(self) -> bool:
        return not self.limits and bool(self.sources)

    def coverage_sentence(self) -> str:
        if self.is_exhaustive:
            return (
                f"Searched {len(self.factories_searched)} factories across "
                f"{len(self.fee_tiers_searched)} fee tiers and "
                f"{len(self.quote_assets_searched)} quote assets over blocks "
                f"{self.from_block}–{self.to_block}."
            )
        return (
            "Pool discovery is partial: "
            + "; ".join(self.limits or ["no discovery source was declared"])
            + ". Side pools outside this search are not ruled out."
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "sources": list(self.sources),
            "factories_searched": [to_checksum(item) for item in self.factories_searched],
            "fee_tiers_searched": list(self.fee_tiers_searched),
            "quote_assets_searched": [
                to_checksum(item) for item in self.quote_assets_searched
            ],
            "from_block": self.from_block,
            "to_block": self.to_block,
            "exhaustive": self.is_exhaustive,
            "limits": list(self.limits),
            "coverage": self.coverage_sentence(),
            "pools": list(self.pools),
        }
