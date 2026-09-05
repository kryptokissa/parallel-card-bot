"""Keccak-256, pure Python, no dependencies.

The pack ships its own Keccak so that address checksums, event topics,
function selectors and Uniswap v4 PoolIds can be derived offline, on any
machine, with nothing installed beyond CPython. ``hashlib.sha3_256`` is
*not* a substitute: it implements FIPS-202 SHA3, which uses different
padding and produces different digests than the Keccak used by Ethereum.
"""

from __future__ import annotations

_MASK = (1 << 64) - 1
_RATE_BYTES = 136  # 1600-bit state minus 2*256-bit capacity

_ROUND_CONSTANTS = (
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
)

# _ROTATION[x][y]
_ROTATION = (
    (0, 36, 3, 41, 18),
    (1, 44, 10, 45, 2),
    (62, 6, 43, 15, 61),
    (28, 55, 25, 21, 56),
    (27, 20, 39, 8, 14),
)


def _rotl(value: int, shift: int) -> int:
    return ((value << shift) | (value >> (64 - shift))) & _MASK if shift else value


def _permute(lanes: list[list[int]]) -> None:
    for round_index in range(24):
        parity = [
            lanes[x][0] ^ lanes[x][1] ^ lanes[x][2] ^ lanes[x][3] ^ lanes[x][4]
            for x in range(5)
        ]
        theta = [
            parity[(x - 1) % 5] ^ _rotl(parity[(x + 1) % 5], 1) for x in range(5)
        ]
        for x in range(5):
            for y in range(5):
                lanes[x][y] ^= theta[x]

        rotated = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                rotated[y][(2 * x + 3 * y) % 5] = _rotl(lanes[x][y], _ROTATION[x][y])

        for x in range(5):
            for y in range(5):
                lanes[x][y] = rotated[x][y] ^ (
                    (~rotated[(x + 1) % 5][y] & _MASK) & rotated[(x + 2) % 5][y]
                )

        lanes[0][0] ^= _ROUND_CONSTANTS[round_index]


def keccak256(data: bytes) -> bytes:
    """Return the 32-byte Keccak-256 digest of ``data``."""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("keccak256 expects bytes")
    message = bytearray(data)

    # Keccak (pre-FIPS) padding: 0x01 ... 0x80, or a single 0x81 when only
    # one byte of room is left in the block.
    pad_len = _RATE_BYTES - (len(message) % _RATE_BYTES)
    if pad_len == 1:
        message.append(0x81)
    else:
        message.append(0x01)
        message.extend(b"\x00" * (pad_len - 2))
        message.append(0x80)

    lanes = [[0] * 5 for _ in range(5)]
    for offset in range(0, len(message), _RATE_BYTES):
        block = message[offset : offset + _RATE_BYTES]
        for lane_index in range(_RATE_BYTES // 8):
            x, y = lane_index % 5, lane_index // 5
            lanes[x][y] ^= int.from_bytes(
                block[lane_index * 8 : lane_index * 8 + 8], "little"
            )
        _permute(lanes)

    out = bytearray()
    for lane_index in range(4):  # 32 bytes squeezed from the first four lanes
        x, y = lane_index % 5, lane_index // 5
        out.extend(lanes[x][y].to_bytes(8, "little"))
    return bytes(out)


def keccak_hex(data: bytes) -> str:
    """Keccak-256 as a ``0x``-prefixed lowercase hex string."""
    return "0x" + keccak256(data).hex()


def selector(signature: str) -> str:
    """4-byte function selector for a canonical signature.

    ``selector("transfer(address,uint256)") == "0xa9059cbb"``
    """
    return "0x" + keccak256(signature.encode("ascii")).hex()[:8]


def event_topic(signature: str) -> str:
    """topic0 for a canonical event signature."""
    return keccak_hex(signature.encode("ascii"))
