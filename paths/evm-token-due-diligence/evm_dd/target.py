"""The target packet: one frozen description of what is under review.

Every lane of a diligence run — a person, a parallel agent, a later
report generator — works from this one object. It carries the requested
target, the chain the endpoint actually answered for, the pin, the
metadata (each field resolved *or explicitly unresolved*), what code runs,
and the scope of addresses in play.

Metadata has no defaults. A token whose ``decimals()`` does not answer has
unknown decimals; assuming 18 turns a display bug into a wrong number in
every downstream balance.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping

from evm_dd.addresses import TargetRef, normalize, to_checksum
from evm_dd.keccak import keccak_hex
from evm_dd.pins import BlockPin


# Above this, JSON numbers stop being exact in every consumer that matters —
# JavaScript included. Base-unit amounts are serialised as decimal strings so
# a supply of 10**24 survives the trip to a report and a page intact.
JSON_SAFE_INTEGER = 2**53 - 1


@dataclass(frozen=True)
class Resolved:
    """A metadata value that the target itself answered for."""

    value: Any
    method: str
    raw: str = ""

    @property
    def is_resolved(self) -> bool:
        return True

    def to_dict(self) -> dict[str, Any]:
        value = self.value
        if isinstance(value, int) and not isinstance(value, bool):
            if abs(value) > JSON_SAFE_INTEGER:
                value = str(value)
        return {
            "state": "resolved",
            "value": value,
            "method": self.method,
            "raw": self.raw,
        }


@dataclass(frozen=True)
class Unresolved:
    """A metadata value that could not be established. Never a default."""

    reason: str
    attempted: str = ""

    @property
    def is_resolved(self) -> bool:
        return False

    @property
    def value(self) -> None:
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": "unresolved",
            "value": None,
            "reason": self.reason,
            "attempted": self.attempted,
        }


MetadataField = Resolved | Unresolved

METADATA_FIELDS = ("name", "symbol", "decimals", "total_supply")


@dataclass(frozen=True)
class ScopeAddress:
    """One address in the review, with how it got here and what it is."""

    address: str
    chain_id: int
    role: str
    provenance: str
    runtime_status: str = "unknown"  # "code" | "no_code" | "unknown"
    runtime_hash: str = ""
    material: bool = True
    label: str = ""

    _RUNTIME_STATES = ("code", "no_code", "unknown")

    def __post_init__(self) -> None:
        object.__setattr__(self, "address", normalize(self.address))
        if self.runtime_status not in self._RUNTIME_STATES:
            raise ValueError(
                f"runtime_status must be one of {self._RUNTIME_STATES}, "
                f"got {self.runtime_status!r}"
            )
        if not self.role.strip():
            raise ValueError(f"{self.address}: every scope address needs a role")
        if not self.provenance.strip():
            raise ValueError(
                f"{self.address}: every scope address needs a provenance — how "
                "it entered the review, so a reader can check the search"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "address": to_checksum(self.address),
            "chain_id": self.chain_id,
            "role": self.role,
            "provenance": self.provenance,
            "runtime_status": self.runtime_status,
            "runtime_hash": self.runtime_hash,
            "material": self.material,
            "label": self.label,
        }


@dataclass
class TargetPacket:
    """Frozen once built. Shared verbatim by every lane of the run."""

    requested: TargetRef
    observed_chain_id: int
    pin: BlockPin
    header: Mapping[str, Any] = field(default_factory=dict)
    metadata: dict[str, MetadataField] = field(default_factory=dict)
    runtime_hash: str = ""
    runtime_size: int = 0
    proxy: Mapping[str, Any] | None = None
    deployment: Mapping[str, Any] | None = None
    scope: list[ScopeAddress] = field(default_factory=list)
    decision_question: str = ""
    scope_statement: str = ""
    materiality: str = ""
    known_limitations: list[str] = field(default_factory=list)
    endpoint_identity: str = ""
    additional_pins: dict[int, BlockPin] = field(default_factory=dict)

    # -- integrity --------------------------------------------------------

    def identity_defects(self) -> list[str]:
        """Ways the packet contradicts itself. Must be empty before any read."""
        problems: list[str] = []
        if self.observed_chain_id != self.requested.chain_id:
            problems.append(
                f"requested chain {self.requested.chain_id} but the endpoint "
                f"answered for chain {self.observed_chain_id}. This is the "
                "same-symbol-different-chain failure; stop rather than "
                "substitute a token."
            )
        if self.pin.chain_id != self.requested.chain_id:
            problems.append(
                f"pin is on chain {self.pin.chain_id}, target is on chain "
                f"{self.requested.chain_id}"
            )
        if self.header:
            problems.extend(
                f"pin disagrees with the captured header: {item}"
                for item in self.pin.verify_against_header(self.header)
            )
        for entry in self.scope:
            if entry.chain_id <= 0:
                problems.append(f"{entry.address}: scope entry has no chain")
        if self.runtime_size == 0 and self.runtime_hash:
            problems.append(
                "runtime hash recorded for an address with no code at the pin"
            )
        return problems

    def unresolved_metadata(self) -> list[str]:
        return [
            name
            for name in METADATA_FIELDS
            if name not in self.metadata or not self.metadata[name].is_resolved
        ]

    def material_scope(self) -> list[ScopeAddress]:
        return [entry for entry in self.scope if entry.material]

    def pin_for(self, chain_id: int) -> BlockPin:
        if chain_id == self.pin.chain_id:
            return self.pin
        pin = self.additional_pins.get(chain_id)
        if pin is None:
            raise KeyError(
                f"chain {chain_id} has no pin in this packet; pin it before "
                "reading it, and never borrow another chain's pin"
            )
        return pin

    # -- serialisation ----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested": {
                "chain_id": self.requested.chain_id,
                "address": to_checksum(self.requested.address),
                "caip10": self.requested.caip10,
            },
            "observed_chain_id": self.observed_chain_id,
            "endpoint_identity": self.endpoint_identity,
            "pin": self.pin.to_dict(),
            "additional_pins": {
                str(chain): pin.to_dict()
                for chain, pin in sorted(self.additional_pins.items())
            },
            "header_digest": self.header_digest(),
            "metadata": {
                name: self.metadata[name].to_dict()
                for name in METADATA_FIELDS
                if name in self.metadata
            },
            "unresolved_metadata": self.unresolved_metadata(),
            "runtime": {"hash": self.runtime_hash, "size": self.runtime_size},
            "proxy": dict(self.proxy) if self.proxy else None,
            "deployment": dict(self.deployment) if self.deployment else None,
            "scope": [entry.to_dict() for entry in self.scope],
            "decision_question": self.decision_question,
            "scope_statement": self.scope_statement,
            "materiality": self.materiality,
            "known_limitations": list(self.known_limitations),
            "identity_defects": self.identity_defects(),
        }

    def header_digest(self) -> str:
        """Digest of the captured header, so a swapped header is detectable."""
        if not self.header:
            return ""
        canonical = json.dumps(
            {key: self.header[key] for key in sorted(self.header)},
            separators=(",", ":"),
            default=str,
        )
        return keccak_hex(canonical.encode("utf-8"))

    def freeze(self) -> tuple[str, dict[str, Any]]:
        """Canonical form plus its digest.

        Parallel lanes quote the digest. Two lanes that disagree on it were
        not looking at the same target, and their findings must not be
        merged.
        """
        payload = self.to_dict()
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return keccak_hex(canonical.encode("utf-8")), payload


def metadata_from_calls(
    *,
    name: Any = None,
    symbol: Any = None,
    decimals: Any = None,
    total_supply: Any = None,
    failures: Mapping[str, str] | None = None,
) -> dict[str, MetadataField]:
    """Build the metadata block, keeping failures explicitly unresolved."""
    failures = dict(failures or {})
    fields: dict[str, MetadataField] = {}
    supplied = {
        "name": (name, "eth_call name() -> string"),
        "symbol": (symbol, "eth_call symbol() -> string"),
        "decimals": (decimals, "eth_call decimals() -> uint8"),
        "total_supply": (total_supply, "eth_call totalSupply() -> uint256"),
    }
    for key, (value, method) in supplied.items():
        if key in failures:
            fields[key] = Unresolved(reason=failures[key], attempted=method)
        elif value is None:
            fields[key] = Unresolved(
                reason="the target did not return a decodable value",
                attempted=method,
            )
        else:
            fields[key] = Resolved(value=value, method=method)
    return fields
