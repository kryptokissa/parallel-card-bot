"""The join: collected evidence in, a self-validating report out.

Three shapes matter — a clean immutable token, one with privileged
capability behind a proxy, and an endpoint that fell over. In all three the
report must satisfy the pack's own validator, and the surfaces this pass
never reached must read `not_checked` rather than vanish.
"""

from __future__ import annotations

import pytest

from evm_dd.abi import encode
from evm_dd.addresses import TargetRef
from evm_dd.assess import assess
from evm_dd.collect import build_packet, scan_executing_runtime
from evm_dd.evidence import Ledger, Status
from evm_dd.keccak import selector
from evm_dd.manifest import Declarations, build_manifest, validate_report
from evm_dd.proxy import SLOT_EIP1967_ADMIN, SLOT_EIP1967_IMPLEMENTATION
from evm_dd.rpc import ReadOnlyRpc, RpcUnavailable
from helpers import (
    ADMIN,
    IMPLEMENTATION,
    TOKEN,
    ZERO_WORD,
    failing_transport,
    header,
    scripted_transport,
    word,
)

# A plain dispatcher: transfer/balanceOf only, and no DELEGATECALL.
PLAIN_RUNTIME = "0x" + "63" + selector("transfer(address,uint256)")[2:] + "63" + selector("balanceOf(address)")[2:] + "00"
# A dispatcher exposing mint, behind a proxy that delegates.
MINTING_RUNTIME = "0x" + "63" + selector("mint(address,uint256)")[2:] + "00"
PROXY_RUNTIME = "0x" + "63" + selector("owner()")[2:] + "f4" + "00"


def _responder(*, proxy: bool, impl_runtime: str, target_runtime: str):
    def respond(method: str, params: list) -> object:
        if method == "eth_chainId":
            return hex(1)
        if method == "eth_getBlockByNumber":
            return header()
        if method == "eth_getCode":
            target = str(params[0]).lower()
            if target == IMPLEMENTATION.lower():
                return impl_runtime
            if target == ADMIN.lower():
                return "0x"          # an EOA admin
            return target_runtime
        if method == "eth_getStorageAt":
            if not proxy:
                return ZERO_WORD
            slot = params[1]
            if slot == SLOT_EIP1967_IMPLEMENTATION:
                return word(IMPLEMENTATION)
            if slot == SLOT_EIP1967_ADMIN:
                return word(ADMIN)
            return ZERO_WORD
        if method == "eth_call":
            data = params[0]["data"]
            if data.startswith(selector("name()")):
                return "0x" + encode(["string"], ["Example"]).hex()
            if data.startswith(selector("symbol()")):
                return "0x" + encode(["string"], ["EX"]).hex()
            if data.startswith(selector("decimals()")):
                return "0x" + (18).to_bytes(32, "big").hex()
            if data.startswith(selector("totalSupply()")):
                return "0x" + (10**24).to_bytes(32, "big").hex()
            return "0x"
        raise KeyError(method)

    return respond


def _run(*, proxy: bool, impl_runtime: str = "0x", target_runtime: str = PLAIN_RUNTIME):
    client = ReadOnlyRpc(
        endpoint="https://rpc.example.org",
        chain_id=1,
        transport=scripted_transport(
            _responder(proxy=proxy, impl_runtime=impl_runtime, target_runtime=target_runtime)
        ),
    )
    ledger = Ledger()
    packet = build_packet(client, TargetRef(1, TOKEN), ledger=ledger)
    scan = scan_executing_runtime(client, packet, ledger)
    report = assess(packet, scan, ledger, source_id="under-test")
    manifest = build_manifest(packet, declarations=Declarations(), report_id="under-test")
    return report, report.to_dict(), manifest


def _rating(payload: dict, dimension: str) -> dict:
    return next(item for item in payload["ratings"] if item["dimension"] == dimension)


def test_a_clean_immutable_token_rates_clear_and_self_validates():
    report, payload, manifest = _run(proxy=False)
    assert report.defects() == [], report.defects()
    controls = _rating(payload, "token_controls")
    assert controls["status"] == Status.CLEAR.value
    assert controls["confidence"] == "proven"
    assert controls["coverage"] and controls["time_basis"]
    result = validate_report(manifest, payload)
    assert result.ok, result.errors


def test_one_clean_surface_never_produces_a_favourable_verdict():
    """The safety property this join could most easily have broken.

    token_controls comes back clean, and the temptation is to call that a
    pass. Ten surfaces were never opened, so the document says so — while
    still leading with what was actually established.
    """
    _, payload, manifest = _run(proxy=False)
    assert payload["verdict"]["call"] == "INSUFFICIENT EVIDENCE"
    assert "Unknown is not a pass" in payload["verdict"]["statement"]
    assert payload["verdict"]["main_reasons"][0].startswith("token_controls: clear")
    assert validate_report(manifest, payload).ok


def test_privileged_capability_behind_a_proxy_is_adverse_and_critical():
    report, payload, manifest = _run(
        proxy=True, impl_runtime=MINTING_RUNTIME, target_runtime=PROXY_RUNTIME
    )
    controls = _rating(payload, "token_controls")
    assert controls["status"] == Status.ADVERSE.value
    assert controls["severity"] == "critical"
    assert payload["verdict"]["call"] == "NO-GO"

    propositions = " ".join(f["proposition"] for f in payload["findings"])
    assert "supply change" in propositions          # the mint entry point
    assert "can be replaced" in propositions        # the proxy
    # An EOA upgrade admin is a materially different finding from a timelock.
    assert "externally owned account" in controls["summary"]
    assert validate_report(manifest, payload).ok


def test_every_unreached_surface_is_named_not_checked():
    _, payload, _ = _run(proxy=False)
    statuses = {item["dimension"]: item["status"] for item in payload["ratings"]}
    assert len(statuses) == 11
    unchecked = [k for k, v in statuses.items() if v == Status.NOT_CHECKED.value]
    assert len(unchecked) == 10
    assert "sellability_and_depth" in unchecked
    # And the reader is told they are open, not left to notice.
    assert set(unchecked) <= set(payload["verdict"]["unresolved_questions"])


def test_a_critical_finding_still_outranks_the_gaps():
    """Unexamined surfaces must not dilute a blocker into "insufficient"."""
    _, payload, _ = _run(proxy=True, impl_runtime=MINTING_RUNTIME, target_runtime=PROXY_RUNTIME)
    assert payload["verdict"]["call"] == "NO-GO"
    assert payload["headline_severity"] == "critical"


def test_an_endpoint_failure_never_becomes_a_rating():
    client = ReadOnlyRpc(
        endpoint="https://rpc.example.org",
        chain_id=1,
        transport=failing_transport("connection reset"),
        max_retries=1,
        sleep=lambda _s: None,
    )
    with pytest.raises(RpcUnavailable) as excinfo:
        build_packet(client, TargetRef(1, TOKEN), ledger=Ledger())
    assert "not a property of the token" in excinfo.value.limitation.consequence
