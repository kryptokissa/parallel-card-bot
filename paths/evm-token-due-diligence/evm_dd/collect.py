"""Building a target packet from a live endpoint, pinned and batched.

Every read after the pin is taken is made *at* that pin, so the packet
describes one state rather than a smear across blocks. Independent reads
go out in one batch. Anything that fails becomes a coverage limitation and
leaves its field explicitly unresolved — never a default, never a guess.
"""

from __future__ import annotations

from typing import Any, Mapping

from evm_dd.abi import AbiError, decode, decode_metadata_string, encode_call
from evm_dd.addresses import TargetRef, normalize, to_checksum
from evm_dd.evidence import (
    CoverageLimitation,
    Evidence,
    EvidenceKind,
    Ledger,
)
from evm_dd.keccak import keccak_hex
from evm_dd.pins import BlockPin
from evm_dd.proxy import STANDARD_SLOTS, resolve_proxy
from evm_dd.rpc import ReadOnlyRpc, RpcUnavailable
from evm_dd.selectors import describe_limits, scan_runtime, strip_hex
from evm_dd.target import (
    ScopeAddress,
    TargetPacket,
    metadata_from_calls,
)

_METADATA_CALLS = (
    ("name", "name()", "string"),
    ("symbol", "symbol()", "string"),
    ("decimals", "decimals()", "uint8"),
    ("total_supply", "totalSupply()", "uint256"),
)


def capture_pin(rpc: ReadOnlyRpc, chain_id: int, *, block: str = "latest") -> tuple[BlockPin, dict[str, Any]]:
    """Pin the chain and keep the header the pin was taken from."""
    header = rpc.call("eth_getBlockByNumber", [block, False])
    if not isinstance(header, Mapping):
        raise RpcUnavailable(
            "block header was not an object",
            CoverageLimitation(
                scope="block header",
                reason=f"eth_getBlockByNumber returned {type(header).__name__}",
                attempted=f"{rpc.identity} eth_getBlockByNumber({block})",
                consequence="the run cannot be pinned, so nothing can be stated as current",
            ),
        )
    pin = BlockPin.from_header(chain_id, header, source=rpc.identity)
    return pin, dict(header)


def build_packet(
    rpc: ReadOnlyRpc,
    requested: TargetRef,
    *,
    ledger: Ledger | None = None,
    decision_question: str = "",
    materiality: str = "",
    block: str = "latest",
) -> TargetPacket:
    """Read the minimum that every later question depends on."""
    ledger = ledger or Ledger()
    observed_chain_id = rpc.verify_chain(requested.chain_id)
    pin, header = capture_pin(rpc, observed_chain_id, block=block)
    tag = pin.block_tag
    address = requested.address

    # One batch: code plus the four metadata getters, all at the pin.
    calls: list[tuple[str, list[Any]]] = [("eth_getCode", [address, tag])]
    for _, signature, _ in _METADATA_CALLS:
        calls.append(
            ("eth_call", [{"to": address, "data": encode_call(signature, [], [])}, tag])
        )
    failures: dict[str, str] = {}
    try:
        results = rpc.batch(calls)
    except RpcUnavailable as exc:
        ledger.limit(exc.limitation)
        results = [None] * len(calls)
        failures = {name: exc.limitation.reason for name, _, _ in _METADATA_CALLS}

    runtime = results[0] if results and isinstance(results[0], str) else "0x"
    runtime_bytes = strip_hex(runtime)
    if not runtime_bytes and not failures:
        ledger.limit(
            CoverageLimitation(
                scope="target runtime",
                reason="no code at the requested address at the pinned block",
                attempted=f"eth_getCode({to_checksum(address)}, {tag})",
                consequence=(
                    "this address is not a contract at this block; it cannot be "
                    "the token under review"
                ),
                retryable=False,
            )
        )

    decoded: dict[str, Any] = {}
    for index, (field_name, signature, out_type) in enumerate(_METADATA_CALLS, start=1):
        raw = results[index] if index < len(results) else None
        if raw in (None, "0x", ""):
            failures.setdefault(
                field_name,
                f"{signature} returned no data at the pinned block "
                "(absent, reverting, or non-standard metadata)",
            )
            continue
        try:
            if out_type == "string":
                value = decode_metadata_string(raw)
                if value is None:
                    raise AbiError("metadata did not decode as string or bytes32")
            else:
                value = decode([out_type], raw)[0]
        except (AbiError, ValueError) as exc:
            failures[field_name] = f"{signature} did not decode: {exc}"
            continue
        decoded[field_name] = value
    metadata = metadata_from_calls(**decoded, failures=failures)

    # Proxy resolution: standard slots, read at the pin, in one batch.
    slot_values: dict[str, str | None] = {}
    slot_list = list(STANDARD_SLOTS)
    try:
        slot_results = rpc.batch(
            [("eth_getStorageAt", [address, slot, tag]) for slot in slot_list]
        )
        slot_values = dict(zip(slot_list, slot_results))
    except RpcUnavailable as exc:
        ledger.limit(exc.limitation)
        slot_values = {slot: None for slot in slot_list}

    def read_slot(_address: str, slot: str) -> str | None:
        return slot_values.get(slot)

    def call_implementation(target_address: str) -> str | None:
        try:
            raw = rpc.call(
                "eth_call",
                [
                    {"to": target_address, "data": encode_call("implementation()", [], [])},
                    tag,
                ],
            )
            return decode(["address"], raw)[0] if raw not in (None, "0x") else None
        except (RpcUnavailable, AbiError, ValueError):
            return None

    resolution = resolve_proxy(
        address, runtime, read_slot, call_implementation=call_implementation
    )

    scope = [
        ScopeAddress(
            address=address,
            chain_id=observed_chain_id,
            role="target token",
            provenance="requested by the user",
            runtime_status="code" if runtime_bytes else "no_code",
            runtime_hash=keccak_hex(runtime_bytes),
            label=str(metadata["symbol"].value or ""),
        )
    ]

    # Whether each related address holds code is worth one batched read: an
    # upgrade admin that is an externally owned account is a materially
    # different finding from one that is a timelock or a multisig, and
    # leaving it "unknown" hides exactly that distinction.
    related: list[tuple[str, str, str]] = []
    if resolution.implementation:
        related.append(
            (
                resolution.implementation,
                "implementation (executing code)",
                f"{resolution.pattern} resolution at the pinned block",
            )
        )
    if resolution.beacon:
        related.append(
            (
                resolution.beacon,
                "beacon (selects the implementation)",
                "eip1967 beacon slot at the pinned block",
            )
        )
    if resolution.upgrade_authority:
        related.append(
            (
                resolution.upgrade_authority,
                "upgrade authority",
                resolution.upgrade_authority_basis,
            )
        )

    codes: list[Any] = [None] * len(related)
    if related:
        try:
            codes = rpc.batch(
                [("eth_getCode", [item[0], tag]) for item in related]
            )
        except RpcUnavailable as exc:
            ledger.limit(exc.limitation)

    for (related_address, role, provenance), code in zip(related, codes):
        if code is None:
            status, related_hash = "unknown", ""
        else:
            related_bytes = strip_hex(code or "0x")
            status = "code" if related_bytes else "no_code"
            related_hash = keccak_hex(related_bytes) if related_bytes else ""
        scope.append(
            ScopeAddress(
                address=related_address,
                chain_id=observed_chain_id,
                role=role,
                provenance=provenance,
                runtime_status=status,
                runtime_hash=related_hash,
            )
        )

    packet = TargetPacket(
        requested=requested,
        observed_chain_id=observed_chain_id,
        pin=pin,
        header=header,
        metadata=metadata,
        runtime_hash=keccak_hex(runtime_bytes),
        runtime_size=len(runtime_bytes),
        proxy=resolution.to_dict(),
        scope=scope,
        decision_question=decision_question,
        materiality=materiality,
        endpoint_identity=rpc.identity,
        known_limitations=[item.reason for item in ledger.limitations],
    )
    return packet


def scan_executing_runtime(
    rpc: ReadOnlyRpc, packet: TargetPacket, ledger: Ledger | None = None
) -> dict[str, Any]:
    """Capability scan of the code that actually runs, at the packet's pin."""
    ledger = ledger or Ledger()
    executing = (packet.proxy or {}).get("executing_address") or packet.requested.address
    try:
        runtime = rpc.call("eth_getCode", [normalize(executing), packet.pin.block_tag]) or "0x"
    except RpcUnavailable as exc:
        ledger.limit(exc.limitation)
        return {
            "executing_address": to_checksum(executing),
            "scanned": False,
            "reason": exc.limitation.reason,
        }
    scan = scan_runtime(runtime)
    payload = scan.to_dict()
    payload["executing_address"] = to_checksum(executing)
    payload["scanned"] = True
    payload["caveats"] = describe_limits(scan)
    payload["evidence"] = Evidence(
        kind=EvidenceKind.RUNTIME_BYTECODE,
        summary=f"runtime of {to_checksum(executing)} at block {packet.pin.number}",
        query=f"eth_getCode({to_checksum(executing)}, {packet.pin.block_tag})",
        result_digest=scan.runtime_hash,
        chain_id=packet.observed_chain_id,
        address=normalize(executing),
        block_number=packet.pin.number,
        source=rpc.identity,
    ).to_dict()
    return payload
