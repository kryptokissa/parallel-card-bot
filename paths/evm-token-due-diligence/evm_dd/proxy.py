"""Proxy, implementation, beacon and upgrade-authority resolution.

Published source is a claim about an address until the deployed runtime is
checked against it, and for a proxy the published source is usually not
where the behaviour lives at all. Nothing downstream — the capability
scan, the control review, the source-correspondence check — is meaningful
until this module has said which code actually runs.

Standard slots are derived from their specification strings at import time
rather than pasted in, so the constants cannot drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from evm_dd.abi import decode_word
from evm_dd.addresses import ZERO_ADDRESS, is_address, normalize, same_address
from evm_dd.keccak import keccak256

_MAX = (1 << 256) - 1


def _slot_minus_one(text: str) -> str:
    return "0x" + ((int.from_bytes(keccak256(text.encode()), "big") - 1) & _MAX).to_bytes(32, "big").hex()


def _slot(text: str) -> str:
    return "0x" + keccak256(text.encode()).hex()


# EIP-1967: keccak256(label) - 1
SLOT_EIP1967_IMPLEMENTATION = _slot_minus_one("eip1967.proxy.implementation")
SLOT_EIP1967_ADMIN = _slot_minus_one("eip1967.proxy.admin")
SLOT_EIP1967_BEACON = _slot_minus_one("eip1967.proxy.beacon")
# EIP-1822 UUPS: keccak256("PROXIABLE")
SLOT_EIP1822_IMPLEMENTATION = _slot("PROXIABLE")
# OpenZeppelin pre-1967 proxies
SLOT_ZEPPELINOS_IMPLEMENTATION = _slot("org.zeppelinos.proxy.implementation")
SLOT_ZEPPELINOS_ADMIN = _slot("org.zeppelinos.proxy.admin")

STANDARD_SLOTS: dict[str, tuple[str, str]] = {
    SLOT_EIP1967_IMPLEMENTATION: ("eip1967_implementation", "implementation"),
    SLOT_EIP1967_ADMIN: ("eip1967_admin", "admin"),
    SLOT_EIP1967_BEACON: ("eip1967_beacon", "beacon"),
    SLOT_EIP1822_IMPLEMENTATION: ("eip1822_implementation", "implementation"),
    SLOT_ZEPPELINOS_IMPLEMENTATION: ("zeppelinos_implementation", "implementation"),
    SLOT_ZEPPELINOS_ADMIN: ("zeppelinos_admin", "admin"),
}

# EIP-1167 minimal proxy: the 20 bytes between these two runtime fragments.
_MINIMAL_PROXY_PREFIX = "363d3d373d3d3d363d73"
_MINIMAL_PROXY_SUFFIX = "5af43d82803e903d91602b57fd5bf3"
# Vyper/0age variants keep the same delegatecall tail with a different head.
_MINIMAL_PROXY_TAILS = (
    "5af43d82803e903d91602b57fd5bf3",
    "5af43d3d93803e602a57fd5bf3",
)


def decode_slot_address(word: str | bytes | None) -> str | None:
    """Read an address out of a 32-byte storage word, or ``None`` if empty."""
    if word is None:
        return None
    try:
        address = decode_word("address", bytes.fromhex(str(word).removeprefix("0x")))
    except Exception:
        return None
    return None if same_address(address, ZERO_ADDRESS) else normalize(address)


def minimal_proxy_target(runtime: str) -> str | None:
    """Extract the delegate target from an EIP-1167-style minimal proxy."""
    code = str(runtime).removeprefix("0x").lower()
    if _MINIMAL_PROXY_PREFIX in code:
        start = code.index(_MINIMAL_PROXY_PREFIX) + len(_MINIMAL_PROXY_PREFIX)
        candidate = "0x" + code[start : start + 40]
        if is_address(candidate) and not same_address(candidate, ZERO_ADDRESS):
            return normalize(candidate)
    for tail in _MINIMAL_PROXY_TAILS:
        if tail in code:
            head = code[: code.index(tail)]
            if len(head) >= 40:
                candidate = "0x" + head[-40:]
                if is_address(candidate) and not same_address(candidate, ZERO_ADDRESS):
                    return normalize(candidate)
    return None


@dataclass
class ProxyResolution:
    """What actually executes at an address, and who can change it."""

    address: str
    runtime_hash: str
    runtime_size: int
    is_proxy: bool = False
    pattern: str = "none"
    implementation: str | None = None
    beacon: str | None = None
    admin_slot_value: str | None = None
    upgrade_authority: str | None = None
    upgrade_authority_basis: str = ""
    slots_read: dict[str, str | None] = field(default_factory=dict)
    unresolved: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def executing_address(self) -> str:
        """The address whose runtime should be scanned for capability."""
        return self.implementation or self.address

    def to_dict(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "runtime_hash": self.runtime_hash,
            "runtime_size": self.runtime_size,
            "is_proxy": self.is_proxy,
            "pattern": self.pattern,
            "implementation": self.implementation,
            "beacon": self.beacon,
            "executing_address": self.executing_address,
            "admin_slot_value": self.admin_slot_value,
            "upgrade_authority": self.upgrade_authority,
            "upgrade_authority_basis": self.upgrade_authority_basis,
            "slots_read": self.slots_read,
            "unresolved": list(self.unresolved),
            "notes": list(self.notes),
        }


SlotReader = Callable[[str, str], str | None]
"""(address, slot) -> 32-byte hex word, or None when the read failed."""


def resolve_proxy(
    address: str,
    runtime: str,
    read_slot: SlotReader,
    *,
    call_implementation: Callable[[str], str | None] | None = None,
) -> ProxyResolution:
    """Resolve the executing implementation behind ``address``.

    ``read_slot`` must raise nothing: a failed read returns ``None`` and is
    recorded as unresolved, never as "not a proxy".
    """
    from evm_dd.keccak import keccak_hex
    from evm_dd.selectors import strip_hex

    raw = strip_hex(runtime)
    resolution = ProxyResolution(
        address=normalize(address),
        runtime_hash=keccak_hex(raw),
        runtime_size=len(raw),
    )

    if not raw:
        resolution.notes.append(
            "No runtime code at this address at the pinned block. It is an "
            "externally owned account, a contract not yet deployed, or one "
            "that has been destroyed — not a token."
        )
        return resolution

    minimal = minimal_proxy_target("0x" + raw.hex())
    if minimal:
        resolution.is_proxy = True
        resolution.pattern = "eip1167_minimal"
        resolution.implementation = minimal
        resolution.upgrade_authority = None
        resolution.upgrade_authority_basis = (
            "minimal proxies hardcode their target in runtime; the delegate "
            "cannot be changed without deploying a new proxy"
        )
        return resolution

    for slot, (label, role) in STANDARD_SLOTS.items():
        word = read_slot(resolution.address, slot)
        resolution.slots_read[label] = word
        if word is None:
            resolution.unresolved.append(f"storage slot {label} could not be read")
            continue
        value = decode_slot_address(word)
        if value is None:
            continue
        if role == "implementation":
            resolution.is_proxy = True
            resolution.pattern = label
            resolution.implementation = value
        elif role == "admin":
            resolution.admin_slot_value = value
            resolution.upgrade_authority = value
            resolution.upgrade_authority_basis = f"{label} storage slot"
        elif role == "beacon":
            resolution.is_proxy = True
            resolution.pattern = label
            resolution.beacon = value

    if resolution.beacon and not resolution.implementation:
        if call_implementation is not None:
            beacon_impl = call_implementation(resolution.beacon)
            if beacon_impl and is_address(beacon_impl):
                resolution.implementation = normalize(beacon_impl)
                resolution.notes.append(
                    "Implementation comes from the beacon. Whoever controls "
                    "the beacon re-points every proxy pointing at it, "
                    "including this one, in a single transaction."
                )
            else:
                resolution.unresolved.append(
                    "beacon implementation() did not resolve"
                )
        else:
            resolution.unresolved.append(
                "beacon present but no implementation reader was supplied"
            )

    if not resolution.is_proxy:
        from evm_dd.selectors import opcodes_present

        if "DELEGATECALL" in opcodes_present(raw):
            resolution.notes.append(
                "The runtime DELEGATECALLs but matches no standard proxy "
                "slot. The delegate target may be computed, stored in a "
                "non-standard slot, or selected per call — resolve it from "
                "storage or a trace before treating this bytecode as the "
                "whole system."
            )
            resolution.unresolved.append("non-standard delegatecall target")

    if resolution.is_proxy and not resolution.upgrade_authority:
        resolution.unresolved.append(
            "upgrade authority not established from standard slots"
        )
        resolution.notes.append(
            "A proxy with no admin in the standard slot is usually UUPS: the "
            "authority lives in the implementation's own access control. "
            "Read it there; do not record the upgrade path as unowned."
        )

    return resolution
