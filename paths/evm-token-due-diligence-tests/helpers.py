"""Fixtures for tests. Every value here is synthetic.

No test in this pack asserts anything about a real token. Addresses are
repeated-byte patterns, block hashes are patterned, and the one place real
data appears is the Uniswap CREATE2 vectors in ``test_pools.py``, which
exist to prove the derivation matches published deployments.
"""

from __future__ import annotations

from typing import Any, Callable

SYNTHETIC = True

TOKEN = "0x1111111111111111111111111111111111111111"
IMPLEMENTATION = "0x2222222222222222222222222222222222222222"
ADMIN = "0x3333333333333333333333333333333333333333"
POOL = "0x4444444444444444444444444444444444444444"
LOCKER = "0x5555555555555555555555555555555555555555"
DEPLOYER = "0x6666666666666666666666666666666666666666"
WETH = "0x7777777777777777777777777777777777777777"

BLOCK_HASH = "0x" + "ab" * 32
PARENT_HASH = "0x" + "cd" * 32
BLOCK_NUMBER = 21_633_856
BLOCK_TIMESTAMP = 1_724_950_000


def header(
    number: int = BLOCK_NUMBER,
    block_hash: str = BLOCK_HASH,
    timestamp: int = BLOCK_TIMESTAMP,
) -> dict[str, Any]:
    return {
        "number": hex(number),
        "hash": block_hash,
        "timestamp": hex(timestamp),
        "parentHash": PARENT_HASH,
    }


def word(value: str) -> str:
    """Right-align an address into a 32-byte storage word."""
    return "0x" + "00" * 12 + value.removeprefix("0x").lower()


ZERO_WORD = "0x" + "00" * 32


def scripted_transport(
    responder: Callable[[str, list[Any]], Any]
) -> Callable[[str, Any, float], Any]:
    """Turn a (method, params) -> result function into an RPC transport."""

    def transport(_url: str, payload: Any, _timeout: float) -> Any:
        requests = payload if isinstance(payload, list) else [payload]
        out = []
        for request in requests:
            try:
                result = responder(request["method"], request.get("params") or [])
            except KeyError as exc:  # unscripted call: surface it loudly
                out.append(
                    {
                        "jsonrpc": "2.0",
                        "id": request["id"],
                        "error": {"code": -32601, "message": f"unscripted {exc}"},
                    }
                )
                continue
            out.append({"jsonrpc": "2.0", "id": request["id"], "result": result})
        return out

    return transport


def failing_transport(message: str = "execution timeout"):
    def transport(_url: str, _payload: Any, _timeout: float) -> Any:
        raise TimeoutError(message)

    return transport
