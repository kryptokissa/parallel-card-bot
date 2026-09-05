"""Address handling and target identity.

The single most common way an automated token review goes wrong is not a
subtle decoding error: it is answering about a *different token*. A
same-symbol contract on another chain, a router mistaken for the token, a
pool address pasted where the token belongs. Everything in this module
exists to make that failure loud instead of silent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from evm_dd.keccak import keccak256

_HEX_ADDRESS_RE = re.compile(r"\A0x[0-9a-fA-F]{40}\Z")

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

# Addresses conventionally used to make supply unrecoverable. They are
# *conventions*, not protocol features: treat balances here as burned only
# after confirming no code and no known key control.
BURN_ADDRESSES = (
    ZERO_ADDRESS,
    "0x000000000000000000000000000000000000dEaD",
)


class AddressError(ValueError):
    """Raised for anything that is not a well-formed 20-byte hex address."""


def is_address(value: object) -> bool:
    return isinstance(value, str) and bool(_HEX_ADDRESS_RE.match(value))


def normalize(value: str) -> str:
    """Lowercase canonical form, used as the key for every cache and index."""
    if not is_address(value):
        raise AddressError(f"not a 20-byte hex address: {value!r}")
    return value.lower()


def to_checksum(value: str) -> str:
    """EIP-55 mixed-case checksum form, used for anything a human reads."""
    body = normalize(value)[2:]
    digest = keccak256(body.encode("ascii")).hex()
    return "0x" + "".join(
        char.upper() if int(digest[index], 16) >= 8 else char
        for index, char in enumerate(body)
    )


def has_valid_checksum(value: str) -> bool:
    """True when ``value`` is all one case (no claim made) or a valid EIP-55.

    A mixed-case address whose checksum does not verify is a typo or a
    substitution and must never be queried.
    """
    if not is_address(value):
        return False
    body = value[2:]
    if body == body.lower() or body == body.upper():
        return True
    return value == to_checksum(value)


def require_address(value: str, *, field: str = "address") -> str:
    if not is_address(value):
        raise AddressError(f"{field} is not a 20-byte hex address: {value!r}")
    if not has_valid_checksum(value):
        raise AddressError(
            f"{field} has a mixed-case body whose EIP-55 checksum does not "
            f"verify — treat as a typo or substitution, not an address: {value}"
        )
    return normalize(value)


def same_address(left: str, right: str) -> bool:
    try:
        return normalize(left) == normalize(right)
    except AddressError:
        return False


def is_burn_address(value: str) -> bool:
    return any(same_address(value, burn) for burn in BURN_ADDRESSES)


def short(value: str) -> str:
    checksummed = to_checksum(value)
    return f"{checksummed[:6]}…{checksummed[-4:]}"


@dataclass(frozen=True)
class TargetRef:
    """One contract on one chain. The unit of identity for the whole run.

    Two refs are equal only when *both* the chain id and the address match.
    Anything that takes a chain and an address separately can drift; this
    type is passed around so that it cannot.
    """

    chain_id: int
    address: str

    def __post_init__(self) -> None:
        if not isinstance(self.chain_id, int) or isinstance(self.chain_id, bool):
            raise AddressError(f"chain_id must be an int, got {self.chain_id!r}")
        if self.chain_id <= 0:
            raise AddressError(f"chain_id must be positive, got {self.chain_id}")
        object.__setattr__(self, "address", require_address(self.address))

    @property
    def caip10(self) -> str:
        """CAIP-10 account id: the least ambiguous way to write a target."""
        return f"eip155:{self.chain_id}:{to_checksum(self.address)}"

    def matches(self, chain_id: int, address: str) -> bool:
        return self.chain_id == chain_id and same_address(self.address, address)

    def __str__(self) -> str:
        return self.caip10


def parse_target(value: str) -> TargetRef:
    """Parse ``eip155:1:0xabc…``, ``1:0xabc…`` or ``0xabc…@1``."""
    text = str(value).strip()
    if text.lower().startswith("eip155:"):
        text = text[len("eip155:") :]
    if "@" in text:
        address, _, chain = text.partition("@")
    elif ":" in text:
        chain, _, address = text.partition(":")
    else:
        raise AddressError(
            "a target must name its chain: use 'eip155:<chainId>:<address>', "
            f"'<chainId>:<address>' or '<address>@<chainId>' (got {value!r})"
        )
    try:
        chain_id = int(str(chain).strip(), 0)
    except ValueError as exc:
        raise AddressError(f"chain id is not a number: {chain!r}") from exc
    return TargetRef(chain_id=chain_id, address=str(address).strip())
