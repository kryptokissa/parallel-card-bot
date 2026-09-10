"""Using the endpoint the host already provides.

A path running on the Wayfinder runtime does not need an RPC URL of its
own: the runtime resolves one per chain, from the operator's configured
`strategy.rpc_urls` if present and otherwise from Wayfinder's own
key-authenticated endpoint. Requiring the operator to supply an endpoint
anyway — as this pack originally did — makes every install fail on its
first run for no reason.

The import of the host runtime is guarded. Outside Wayfinder the pack is
still standard library only and still takes `--rpc`; this module simply
reports that no host endpoint is available. The runtime is a declared
dependency of the *path* (`wfpath.yaml` names `wayfinder-paths`), never of
the analysis code.

Nothing here weakens the read-only guarantee: the endpoint is handed to the
same `ReadOnlyRpc`, with the same allowlist, and the host's credential
travels as a header that is never written into evidence.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HostEndpoint:
    """An endpoint the host resolved, plus how it was found."""

    url: str
    headers: dict[str, str]
    origin: str

    def describe(self) -> str:
        return f"host-provided endpoint ({self.origin})"


class HostUnavailable(RuntimeError):
    """No host runtime, or it resolved no endpoint for this chain."""


def resolve(chain_id: int) -> HostEndpoint:
    """Ask the Wayfinder runtime for a read endpoint on ``chain_id``.

    Raises ``HostUnavailable`` when the pack is running standalone, which
    is the ordinary case for a local run.
    """
    try:
        from wayfinder_paths.core.config import (  # type: ignore[import-not-found]
            get_api_base_url,
            get_api_key,
            get_rpc_urls,
        )
    except ImportError as exc:  # running outside the Wayfinder runtime
        raise HostUnavailable(
            "the Wayfinder runtime is not importable, so no host endpoint "
            "exists; pass --rpc or set EVM_RPC_URL"
        ) from exc

    # An operator-configured endpoint wins: it is their node, their limits.
    configured = get_rpc_urls() or {}
    candidate = configured.get(str(chain_id)) or configured.get(chain_id)
    if isinstance(candidate, (list, tuple)):
        candidate = candidate[0] if candidate else None
    if isinstance(candidate, str) and candidate.strip():
        return HostEndpoint(
            url=candidate.strip(),
            headers={},
            origin=f"strategy.rpc_urls[{chain_id}]",
        )

    # Otherwise the runtime's own per-chain endpoint, authenticated by key.
    base = str(get_api_base_url() or "").rstrip("/")
    if not base:
        raise HostUnavailable("the host runtime reports no API base url")
    api_key = get_api_key()
    if not api_key:
        raise HostUnavailable(
            "the host runtime has no API key, so its endpoint cannot be used"
        )
    return HostEndpoint(
        url=f"{base}/blockchain/rpc/{chain_id}/",
        headers={"X-API-KEY": str(api_key)},
        origin="wayfinder runtime rpc",
    )
