"""Block pins: the timestamp on every claim.

"The owner cannot mint" is not a fact about a token, it is a fact about a
token *at a block*. A pin is the block number, the block hash and the UTC
timestamp taken together, captured from the same header, so a reader can
re-run any read and get the same bytes — and so a stale report is
detectable rather than merely old.

Each chain in a run gets its own pin. Nothing here lets one chain's pin
stand in for another's.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

_HASH_RE = re.compile(r"\A0x[0-9a-fA-F]{64}\Z")
_ZERO_HASH = "0x" + "0" * 64

# Ethereum mainnet genesis. Nothing on an EVM chain predates it, so a
# timestamp below this is a placeholder or a decoding error, not history.
_EARLIEST_PLAUSIBLE = 1_438_269_973


class PinError(ValueError):
    pass


def _to_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise PinError(f"{field} must be a number, got a bool")
    if isinstance(value, int):
        return value
    text = str(value).strip()
    try:
        return int(text, 16) if text.lower().startswith("0x") else int(text)
    except ValueError as exc:
        raise PinError(f"{field} is not a number: {value!r}") from exc


@dataclass(frozen=True)
class BlockPin:
    """One chain, one block, captured as a unit."""

    chain_id: int
    number: int
    block_hash: str
    timestamp: int
    parent_hash: str = ""
    source: str = ""

    def __post_init__(self) -> None:
        if self.chain_id <= 0:
            raise PinError(f"chain_id must be positive, got {self.chain_id}")
        if self.number <= 0:
            raise PinError(
                f"block number must be positive, got {self.number}; a pin of 0 "
                "or 'latest' is a placeholder, not a pin"
            )
        if not _HASH_RE.match(self.block_hash or ""):
            raise PinError(f"block hash is not a 32-byte hex hash: {self.block_hash!r}")
        if self.block_hash.lower() == _ZERO_HASH:
            raise PinError("block hash is the zero hash — a placeholder, not a pin")
        if self.timestamp <= 0:
            raise PinError(f"timestamp must be positive, got {self.timestamp}")
        if self.timestamp < _EARLIEST_PLAUSIBLE:
            raise PinError(
                f"timestamp {self.timestamp} predates EVM genesis — placeholder "
                "or a units error (seconds, not milliseconds)"
            )
        object.__setattr__(self, "block_hash", self.block_hash.lower())
        if self.parent_hash:
            object.__setattr__(self, "parent_hash", self.parent_hash.lower())

    @property
    def utc(self) -> str:
        return (
            datetime.fromtimestamp(self.timestamp, tz=timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )

    def age_seconds(self, now: int | None = None) -> int:
        reference = now if now is not None else int(datetime.now(tz=timezone.utc).timestamp())
        return reference - self.timestamp

    def check_not_in_future(self, now: int, tolerance_seconds: int = 900) -> None:
        if self.timestamp > now + tolerance_seconds:
            raise PinError(
                f"pin timestamp {self.utc} is ahead of the clock by "
                f"{self.timestamp - now}s — fabricated or a clock fault"
            )

    def describe(self) -> str:
        return f"chain {self.chain_id} block {self.number} ({self.block_hash[:10]}…) at {self.utc}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain_id": self.chain_id,
            "number": self.number,
            "block_hash": self.block_hash,
            "parent_hash": self.parent_hash,
            "timestamp": self.timestamp,
            "utc": self.utc,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "BlockPin":
        try:
            return cls(
                chain_id=_to_int(payload["chain_id"], "chain_id"),
                number=_to_int(payload["number"], "number"),
                block_hash=str(payload["block_hash"]),
                timestamp=_to_int(payload["timestamp"], "timestamp"),
                parent_hash=str(payload.get("parent_hash") or ""),
                source=str(payload.get("source") or ""),
            )
        except KeyError as exc:
            raise PinError(f"pin is missing {exc.args[0]}") from exc

    @classmethod
    def from_header(
        cls, chain_id: int, header: Mapping[str, Any], *, source: str = ""
    ) -> "BlockPin":
        """Capture a pin from an ``eth_getBlockByNumber`` header."""
        if not isinstance(header, Mapping):
            raise PinError(f"block header is not an object: {type(header).__name__}")
        missing = [key for key in ("number", "hash", "timestamp") if key not in header]
        if missing:
            raise PinError(f"block header is missing {', '.join(missing)}")
        return cls(
            chain_id=chain_id,
            number=_to_int(header["number"], "number"),
            block_hash=str(header["hash"]),
            timestamp=_to_int(header["timestamp"], "timestamp"),
            parent_hash=str(header.get("parentHash") or ""),
            source=source,
        )

    def verify_against_header(self, header: Mapping[str, Any]) -> list[str]:
        """Return the ways this pin disagrees with a freshly fetched header."""
        problems: list[str] = []
        try:
            observed = BlockPin.from_header(self.chain_id, header)
        except PinError as exc:
            return [f"header could not be read: {exc}"]
        if observed.number != self.number:
            problems.append(f"number {self.number} != header {observed.number}")
        if observed.block_hash != self.block_hash:
            problems.append(f"hash {self.block_hash} != header {observed.block_hash}")
        if observed.timestamp != self.timestamp:
            problems.append(
                f"timestamp {self.timestamp} != header {observed.timestamp}"
            )
        return problems

    @property
    def block_tag(self) -> str:
        """The hex block tag to pass to every pinned read."""
        return hex(self.number)


@dataclass
class PinSet:
    """Pins for a multi-chain run. One chain never borrows another's pin."""

    pins: dict[int, BlockPin]

    def __init__(self, pins: dict[int, BlockPin] | None = None) -> None:
        self.pins = dict(pins or {})

    def add(self, pin: BlockPin) -> BlockPin:
        existing = self.pins.get(pin.chain_id)
        if existing and existing.block_hash != pin.block_hash:
            raise PinError(
                f"chain {pin.chain_id} already pinned at block {existing.number}; "
                "re-pinning mid-run would mix two states in one report"
            )
        self.pins[pin.chain_id] = pin
        return pin

    def require(self, chain_id: int) -> BlockPin:
        pin = self.pins.get(chain_id)
        if pin is None:
            raise PinError(
                f"no pin for chain {chain_id}: pin the chain before reading it, "
                "and never reuse another chain's pin"
            )
        return pin

    def to_dict(self) -> dict[str, Any]:
        return {str(chain): pin.to_dict() for chain, pin in sorted(self.pins.items())}
