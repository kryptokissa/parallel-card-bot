"""A JSON-RPC client that cannot write.

The read-only guarantee is structural, not a promise in a document: the
client holds an allowlist of read methods and raises on anything else, so
there is no code path from this pack to a signature or a broadcast. State
mutating methods (``evm_*``, ``anvil_*``, ``hardhat_*``) unlock only on a
client explicitly constructed in fork mode, and only when the endpoint
resolves to loopback — a disposable local fork. Results obtained that way
are tagged counterfactual all the way to the report.

Only the standard library is used, so the pack runs wherever CPython does.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

from evm_dd.evidence import CoverageLimitation, redact

READ_METHODS = frozenset(
    {
        "eth_blockNumber",
        "eth_call",
        "eth_chainId",
        "eth_createAccessList",
        "eth_estimateGas",
        "eth_feeHistory",
        "eth_gasPrice",
        "eth_getBalance",
        "eth_getBlockByHash",
        "eth_getBlockByNumber",
        "eth_getBlockReceipts",
        "eth_getCode",
        "eth_getLogs",
        "eth_getProof",
        "eth_getStorageAt",
        "eth_getTransactionByHash",
        "eth_getTransactionCount",
        "eth_getTransactionReceipt",
        "eth_maxPriorityFeePerGas",
        "eth_syncing",
        "net_version",
        "web3_clientVersion",
        # Read-only tracing. Never mutates chain state.
        "debug_traceCall",
        "debug_traceTransaction",
        "trace_call",
        "trace_transaction",
        "trace_replayTransaction",
    }
)

# Only ever reachable from a fork client bound to loopback.
FORK_METHODS = frozenset(
    {
        "anvil_impersonateAccount",
        "anvil_setBalance",
        "anvil_setCode",
        "anvil_setStorageAt",
        "anvil_stopImpersonatingAccount",
        "eth_sendTransaction",
        "eth_sendRawTransaction",
        "evm_increaseTime",
        "evm_mine",
        "evm_revert",
        "evm_setNextBlockTimestamp",
        "evm_snapshot",
        "hardhat_impersonateAccount",
        "hardhat_setBalance",
        "hardhat_setCode",
        "hardhat_setStorageAt",
        "hardhat_stopImpersonatingAccount",
    }
)

# Never reachable, on any client, in any mode.
FORBIDDEN_METHODS = frozenset(
    {
        "eth_accounts",
        "eth_sign",
        "eth_signTransaction",
        "eth_signTypedData",
        "eth_signTypedData_v3",
        "eth_signTypedData_v4",
        "personal_importRawKey",
        "personal_listAccounts",
        "personal_newAccount",
        "personal_sendTransaction",
        "personal_sign",
        "personal_unlockAccount",
    }
)

# Hostnames that count as loopback when *refusing* a fork endpoint. This is a
# deny-everything-else guard, not an origin: the pack never connects anywhere
# on its own, ships no endpoint, and only ever contacts a host the operator
# passes in. Removing these values would remove the refusal, not a dependency.
_LOOPBACK_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"})


class RpcError(RuntimeError):
    """Base class. Never surfaces as a finding about the token."""


class WriteAttemptError(RpcError):
    """A non-read method was requested. Always a bug, never a network issue."""


class ChainMismatchError(RpcError):
    """The endpoint answered for a different chain than the one requested."""


class RpcUnavailable(RpcError):
    """The read could not be completed. Carries the coverage limitation."""

    def __init__(self, message: str, limitation: CoverageLimitation) -> None:
        super().__init__(message)
        self.limitation = limitation


def endpoint_identity(url: str) -> str:
    """A stable, credential-free name for an endpoint, safe to write down."""
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return "<unparseable endpoint>"
    host = parsed.hostname or "<no host>"
    port = f":{parsed.port}" if parsed.port else ""
    depth = len([part for part in parsed.path.split("/") if part])
    suffix = f"/…({depth} path segments)" if depth else ""
    return f"{parsed.scheme}://{host}{port}{suffix}"


def is_loopback_endpoint(url: str) -> bool:
    try:
        host = urllib.parse.urlsplit(url).hostname
    except ValueError:
        return False
    return (host or "").lower() in _LOOPBACK_HOSTNAMES


@dataclass
class RpcStats:
    requests: int = 0
    batched_calls: int = 0
    cache_hits: int = 0
    retries: int = 0
    failures: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "requests": self.requests,
            "batched_calls": self.batched_calls,
            "cache_hits": self.cache_hits,
            "retries": self.retries,
            "failures": self.failures,
        }


def _http_transport(
    url: str, payload: Any, timeout: float, headers: dict[str, str] | None = None
) -> Any:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "content-type": "application/json",
            "accept": "application/json",
            **(headers or {}),
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


@dataclass
class ReadOnlyRpc:
    """Batched, cached, credential-redacting, write-incapable JSON-RPC."""

    endpoint: str
    chain_id: int | None = None
    timeout: float = 20.0
    max_retries: int = 3
    backoff_seconds: float = 0.75
    fork_mode: bool = False
    # Sent with every request and never persisted: a host-provided endpoint
    # authenticates by header, and that credential must not reach an evidence
    # row. Only `endpoint_identity` — which is URL-derived — is ever recorded.
    headers: dict[str, str] = field(default_factory=dict, repr=False)
    transport: Callable[..., Any] | None = None
    sleep: Callable[[float], None] = time.sleep
    stats: RpcStats = field(default_factory=RpcStats)
    _cache: dict[tuple, Any] = field(default_factory=dict, repr=False)
    _next_id: int = field(default=1, repr=False)

    def __post_init__(self) -> None:
        if self.fork_mode and not is_loopback_endpoint(self.endpoint):
            raise WriteAttemptError(
                "fork mode requires a loopback endpoint (a disposable local "
                f"fork); refusing {endpoint_identity(self.endpoint)}"
            )

    # -- identity ---------------------------------------------------------

    @property
    def identity(self) -> str:
        return endpoint_identity(self.endpoint)

    def verify_chain(self, expected_chain_id: int | None = None) -> int:
        """Read ``eth_chainId`` and bind the client to it.

        Never trust the chain a caller *says* they are on. Every read after
        this point is bound to the chain the endpoint actually answers for.
        """
        raw = self.call("eth_chainId", [])
        observed = int(str(raw), 16) if isinstance(raw, str) else int(raw)
        target = expected_chain_id if expected_chain_id is not None else self.chain_id
        if target is not None and observed != target:
            raise ChainMismatchError(
                f"{self.identity} answers for chain {observed}, not {target}. "
                "Every query, artifact and conclusion must be bound to the "
                "requested chain — stop rather than substitute."
            )
        self.chain_id = observed
        return observed

    # -- calls ------------------------------------------------------------

    def _guard(self, method: str) -> None:
        if method in FORBIDDEN_METHODS:
            raise WriteAttemptError(
                f"{method} is never available from this pack: it signs, "
                "enumerates keys, or unlocks an account."
            )
        if method in READ_METHODS:
            return
        if method in FORK_METHODS:
            if not self.fork_mode:
                raise WriteAttemptError(
                    f"{method} mutates state and is only available on a "
                    "fork-mode client bound to a disposable local fork. "
                    "Results from it are counterfactual, never evidence "
                    "about the live chain."
                )
            return
        raise WriteAttemptError(
            f"{method} is not on the read allowlist. Add it deliberately, "
            "after confirming it cannot mutate state or touch a key."
        )

    def call(self, method: str, params: Sequence[Any] | None = None) -> Any:
        return self.batch([(method, list(params or []))])[0]

    def batch(self, calls: Iterable[tuple[str, Sequence[Any]]]) -> list[Any]:
        """Run independent reads in one round trip, serving repeats from cache."""
        requested = [(method, list(params or [])) for method, params in calls]
        for method, _ in requested:
            self._guard(method)

        results: list[Any] = [None] * len(requested)
        pending: list[tuple[int, str, list[Any]]] = []
        for index, (method, params) in enumerate(requested):
            key = self._cache_key(method, params)
            if key in self._cache:
                self.stats.cache_hits += 1
                results[index] = self._cache[key]
            else:
                pending.append((index, method, params))

        if not pending:
            return results

        payload = []
        for _, method, params in pending:
            payload.append(
                {
                    "jsonrpc": "2.0",
                    "id": self._next_id,
                    "method": method,
                    "params": params,
                }
            )
            self._next_id += 1

        responses = self._send(payload)
        by_id = {item.get("id"): item for item in responses if isinstance(item, dict)}
        for (index, method, params), request in zip(pending, payload):
            response = by_id.get(request["id"])
            if response is None:
                raise RpcUnavailable(
                    f"{method}: no response for request id {request['id']}",
                    CoverageLimitation(
                        scope=f"{method}",
                        reason="endpoint returned an incomplete batch",
                        attempted=f"{self.identity} {method}",
                        consequence="checks depending on this read stay unknown",
                    ),
                )
            if "error" in response and response["error"] is not None:
                error = response["error"]
                message = redact(str(error.get("message", error)))
                raise RpcUnavailable(
                    f"{method}: {message}",
                    CoverageLimitation(
                        scope=f"{method}({json.dumps(params)[:120]})",
                        reason=f"rpc error: {message}",
                        attempted=f"{self.identity} {method}",
                        consequence="checks depending on this read stay unknown",
                        retryable=_looks_retryable(message),
                    ),
                )
            value = response.get("result")
            self._cache[self._cache_key(method, params)] = value
            results[index] = value
            self.stats.batched_calls += 1
        return results

    def _cache_key(self, method: str, params: Sequence[Any]) -> tuple:
        return (self.chain_id, method, json.dumps(params, sort_keys=True, default=str))

    def _send(self, payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
        transport = self.transport or _http_transport
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                self.stats.requests += 1
                raw = (
                    transport(self.endpoint, payload, self.timeout, self.headers)
                    if self.headers
                    else transport(self.endpoint, payload, self.timeout)
                )
                if isinstance(raw, dict):
                    raw = [raw]
                if not isinstance(raw, list):
                    raise RpcError(f"unexpected response shape: {type(raw).__name__}")
                return raw
            except (urllib.error.URLError, OSError, TimeoutError, RpcError) as exc:
                last_error = exc
                if attempt + 1 < self.max_retries:
                    self.stats.retries += 1
                    self.sleep(self.backoff_seconds * (2**attempt))
        self.stats.failures += 1
        reason = redact(str(last_error))
        raise RpcUnavailable(
            f"{self.identity}: {reason}",
            CoverageLimitation(
                scope="rpc transport",
                reason=f"endpoint unreachable or refusing: {reason}",
                attempted=f"{self.identity} ({len(payload)} batched reads)",
                consequence=(
                    "every check that needed this endpoint stays unknown; "
                    "this is a limit of the run, not a property of the token"
                ),
                retryable=True,
            ),
        )


def _looks_retryable(message: str) -> bool:
    lowered = message.lower()
    return any(
        marker in lowered
        for marker in (
            "timeout",
            "timed out",
            "rate limit",
            "too many requests",
            "429",
            "capacity",
            "temporarily",
            "try again",
        )
    )


def fork_client(endpoint: str, **kwargs: Any) -> ReadOnlyRpc:
    """Build a fork-mode client. Raises unless the endpoint is loopback."""
    return ReadOnlyRpc(endpoint=endpoint, fork_mode=True, **kwargs)
