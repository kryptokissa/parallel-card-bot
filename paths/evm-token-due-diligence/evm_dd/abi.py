"""Just enough ABI coding to read a chain honestly, with no dependencies.

Supports the static types and the two dynamic types that token diligence
actually needs: uint*/int*, address, bool, bytes32, string, bytes, and
flat arrays of those. Tuples of static types decode as a flat word
sequence, which is what ``positions()``-style getters return.

Decoding is strict on purpose. A getter that returns something the wrong
shape is a fact about the token — an unusual, possibly deliberate one —
and it must surface as an error to be recorded, not be coerced into a
plausible-looking number.
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from evm_dd.addresses import normalize
from evm_dd.keccak import selector

_WORD = 32
_UINT_RE = re.compile(r"\Auint(\d+)?\Z")
_INT_RE = re.compile(r"\Aint(\d+)?\Z")
_BYTES_N_RE = re.compile(r"\Abytes(\d+)\Z")
_ARRAY_RE = re.compile(r"\A(.+)\[\]\Z")


class AbiError(ValueError):
    pass


def _bits(match: re.Match[str]) -> int:
    return int(match.group(1) or 256)


def is_dynamic(type_name: str) -> bool:
    return type_name in ("string", "bytes") or bool(_ARRAY_RE.match(type_name))


def encode_value(type_name: str, value: Any) -> bytes:
    if type_name == "address":
        return bytes(12) + bytes.fromhex(normalize(str(value))[2:])
    if type_name == "bool":
        return (1 if value else 0).to_bytes(_WORD, "big")
    uint = _UINT_RE.match(type_name)
    if uint:
        bits = _bits(uint)
        number = int(value)
        if number < 0 or number >= (1 << bits):
            raise AbiError(f"{number} does not fit in {type_name}")
        return number.to_bytes(_WORD, "big")
    signed = _INT_RE.match(type_name)
    if signed:
        bits = _bits(signed)
        number = int(value)
        limit = 1 << (bits - 1)
        if number < -limit or number >= limit:
            raise AbiError(f"{number} does not fit in {type_name}")
        return (number & ((1 << 256) - 1)).to_bytes(_WORD, "big")
    fixed_bytes = _BYTES_N_RE.match(type_name)
    if fixed_bytes:
        size = int(fixed_bytes.group(1))
        raw = _as_bytes(value)
        if len(raw) > size:
            raise AbiError(f"{len(raw)} bytes do not fit in {type_name}")
        return raw + bytes(_WORD - len(raw))
    raise AbiError(f"unsupported static type: {type_name}")


def _as_bytes(value: Any) -> bytes:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    text = str(value)
    if text.startswith("0x"):
        return bytes.fromhex(text[2:])
    return text.encode("utf-8")


def encode(types: Sequence[str], values: Sequence[Any]) -> bytes:
    if len(types) != len(values):
        raise AbiError(f"{len(types)} types but {len(values)} values")
    head = b""
    tail = b""
    head_size = _WORD * len(types)
    for type_name, value in zip(types, values):
        if not is_dynamic(type_name):
            head += encode_value(type_name, value)
            continue
        head += (head_size + len(tail)).to_bytes(_WORD, "big")
        tail += _encode_dynamic(type_name, value)
    return head + tail


def _encode_dynamic(type_name: str, value: Any) -> bytes:
    array = _ARRAY_RE.match(type_name)
    if array:
        inner = array.group(1)
        items = list(value)
        return len(items).to_bytes(_WORD, "big") + encode(
            [inner] * len(items), items
        )
    raw = _as_bytes(value)
    padding = (-len(raw)) % _WORD
    return len(raw).to_bytes(_WORD, "big") + raw + bytes(padding)


def encode_call(signature: str, types: Sequence[str], values: Sequence[Any]) -> str:
    """``0x`` + 4-byte selector + encoded arguments, ready for ``eth_call``."""
    return selector(signature) + encode(types, values).hex()


def decode(types: Sequence[str], data: str | bytes) -> list[Any]:
    raw = _as_bytes(data) if not isinstance(data, (bytes, bytearray)) else bytes(data)
    values: list[Any] = []
    for index, type_name in enumerate(types):
        offset = index * _WORD
        if offset + _WORD > len(raw):
            raise AbiError(
                f"return data is {len(raw)} bytes; too short for "
                f"{len(types)} values ({types!r})"
            )
        word = raw[offset : offset + _WORD]
        if is_dynamic(type_name):
            values.append(_decode_dynamic(type_name, raw, int.from_bytes(word, "big")))
        else:
            values.append(decode_word(type_name, word))
    return values


def decode_word(type_name: str, word: bytes) -> Any:
    if len(word) != _WORD:
        raise AbiError(f"expected a 32-byte word, got {len(word)}")
    if type_name == "address":
        if any(word[:12]):
            raise AbiError("address word has dirty high-order bytes")
        return "0x" + word[12:].hex()
    if type_name == "bool":
        number = int.from_bytes(word, "big")
        if number not in (0, 1):
            raise AbiError(f"bool word is neither 0 nor 1: {number}")
        return bool(number)
    if _UINT_RE.match(type_name):
        return int.from_bytes(word, "big")
    signed = _INT_RE.match(type_name)
    if signed:
        bits = _bits(signed)
        number = int.from_bytes(word, "big")
        limit = 1 << (bits - 1)
        number &= (1 << bits) - 1
        return number - (1 << bits) if number >= limit else number
    fixed_bytes = _BYTES_N_RE.match(type_name)
    if fixed_bytes:
        return "0x" + word[: int(fixed_bytes.group(1))].hex()
    raise AbiError(f"unsupported static type: {type_name}")


def _decode_dynamic(type_name: str, raw: bytes, offset: int) -> Any:
    if offset + _WORD > len(raw):
        raise AbiError(f"dynamic offset {offset} is past the end of the data")
    length = int.from_bytes(raw[offset : offset + _WORD], "big")
    body_start = offset + _WORD
    array = _ARRAY_RE.match(type_name)
    if array:
        inner = array.group(1)
        if is_dynamic(inner):
            raise AbiError(f"nested dynamic arrays are not decoded here: {type_name}")
        end = body_start + length * _WORD
        if end > len(raw):
            raise AbiError(f"array of {length} runs past the end of the data")
        return [
            decode_word(inner, raw[body_start + i * _WORD : body_start + (i + 1) * _WORD])
            for i in range(length)
        ]
    end = body_start + length
    if end > len(raw):
        raise AbiError(f"{type_name} of {length} bytes runs past the end of the data")
    body = raw[body_start:end]
    return body.decode("utf-8", errors="replace") if type_name == "string" else "0x" + body.hex()


def decode_metadata_string(data: str | bytes) -> str | None:
    """Decode ``name()``/``symbol()`` from either the ABI or the bytes32 form.

    Returns ``None`` when the value cannot be resolved. Callers must keep
    that as *unresolved* rather than substituting a label from anywhere
    else — a token whose metadata does not decode is a finding of its own.
    """
    raw = _as_bytes(data)
    if not raw:
        return None
    try:
        return str(decode(["string"], raw)[0]).rstrip("\x00") or None
    except (AbiError, ValueError, UnicodeDecodeError):
        pass
    if len(raw) == _WORD:  # non-standard bytes32 metadata (early tokens)
        text = raw.rstrip(b"\x00").decode("utf-8", errors="replace")
        return text or None
    return None
