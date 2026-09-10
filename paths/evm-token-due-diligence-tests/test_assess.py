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


# --- endpoint resolution ---------------------------------------------------


def test_the_host_endpoint_is_used_when_no_url_is_supplied(monkeypatch):
    """A path installed on Wayfinder must work on its first run.

    The runtime already resolves a read endpoint per chain. v0.1.2 demanded
    one anyway and failed every fresh install with
    "no configured RPC is available".
    """
    import sys
    import types

    fake = types.ModuleType("wayfinder_paths.core.config")
    fake.get_rpc_urls = lambda: {}
    fake.get_api_base_url = lambda: "https://api.example.org/api/v1"
    fake.get_api_key = lambda: "wk_secret"
    pkg = types.ModuleType("wayfinder_paths")
    core = types.ModuleType("wayfinder_paths.core")
    monkeypatch.setitem(sys.modules, "wayfinder_paths", pkg)
    monkeypatch.setitem(sys.modules, "wayfinder_paths.core", core)
    monkeypatch.setitem(sys.modules, "wayfinder_paths.core.config", fake)

    from evm_dd import host

    endpoint = host.resolve(8453)
    assert endpoint.url == "https://api.example.org/api/v1/blockchain/rpc/8453/"
    assert endpoint.headers == {"X-API-KEY": "wk_secret"}
    assert "wayfinder runtime" in endpoint.origin


def test_an_operator_configured_endpoint_beats_the_hosts_own(monkeypatch):
    import sys
    import types

    fake = types.ModuleType("wayfinder_paths.core.config")
    fake.get_rpc_urls = lambda: {"1": ["https://node.operator.example/rpc"]}
    fake.get_api_base_url = lambda: "https://api.example.org"
    fake.get_api_key = lambda: "wk_secret"
    monkeypatch.setitem(sys.modules, "wayfinder_paths", types.ModuleType("wayfinder_paths"))
    monkeypatch.setitem(sys.modules, "wayfinder_paths.core", types.ModuleType("wayfinder_paths.core"))
    monkeypatch.setitem(sys.modules, "wayfinder_paths.core.config", fake)

    from evm_dd import host

    endpoint = host.resolve(1)
    assert endpoint.url == "https://node.operator.example/rpc"
    assert endpoint.headers == {}          # their node, their auth, not ours


def test_standalone_runs_still_require_an_explicit_endpoint():
    from evm_dd import host

    with pytest.raises(host.HostUnavailable) as excinfo:
        host.resolve(1)
    assert "--rpc" in str(excinfo.value)


def test_the_host_credential_never_reaches_an_evidence_row():
    """The key authenticates by header; only the URL identity is recorded."""
    from evm_dd.rpc import ReadOnlyRpc

    client = ReadOnlyRpc(
        endpoint="https://api.example.org/api/v1/blockchain/rpc/1/",
        chain_id=1,
        headers={"X-API-KEY": "wk_secret"},
        transport=scripted_transport(lambda m, p: hex(1)),
    )
    assert client.verify_chain(1) == 1
    assert "wk_secret" not in client.identity
    assert "api.example.org" in client.identity


# --- crying wolf -----------------------------------------------------------


def test_the_standard_burnable_surface_is_not_privilege():
    """The false positive the first real run produced.

    `burn(uint256)` burns the caller's own balance and
    `burnFrom(address,uint256)` spends an allowance the holder granted.
    Both are stock OpenZeppelin ERC20Burnable and appear on a large share of
    ordinary tokens. They were classified as supply change (critical) and
    position removal (high) — the latter a selector collision with the
    Uniswap position manager, where `burn(uint256)` means something else
    entirely. Together they returned NO-GO at critical severity on a token
    whose bytecode has no privileged entry point at all.
    """
    from evm_dd.keccak import selector
    from evm_dd.selectors import SEVERITY_BY_CAPABILITY, describe_limits, scan_runtime

    runtime = "0x" + "".join(
        "63" + selector(sig)[2:]
        for sig in (
            "burn(uint256)",
            "burnFrom(address,uint256)",
            "transfer(address,uint256)",
            "owner()",
        )
    ) + "00"
    scan = scan_runtime(runtime)

    assert sorted(scan.capabilities) == ["authority", "holder_burn"]
    assert SEVERITY_BY_CAPABILITY["holder_burn"] == "informational"
    # Reported, not hidden — and the caveat explaining why travels with it.
    assert any("allowance the holder granted" in item for item in describe_limits(scan))


def test_a_burnable_token_does_not_rate_adverse():
    """End to end: the same surface must not drive the verdict."""
    from evm_dd.keccak import selector

    burnable = "0x" + "".join(
        "63" + selector(sig)[2:]
        for sig in ("burn(uint256)", "burnFrom(address,uint256)", "owner()")
    ) + "00"
    _, payload, manifest = _run(proxy=False, target_runtime=burnable)
    controls = _rating(payload, "token_controls")
    assert controls["status"] == Status.CLEAR.value
    assert controls["severity"] == "informational"
    assert payload["verdict"]["call"] != "NO-GO"
    assert validate_report(manifest, payload).ok


def test_burning_a_named_account_without_allowance_is_still_adverse():
    """The genuinely dangerous shape must survive the reclassification."""
    from evm_dd.keccak import selector

    seizing = "0x" + "63" + selector("burn(address,uint256)")[2:] + "00"
    _, payload, _ = _run(proxy=False, target_runtime=seizing)
    controls = _rating(payload, "token_controls")
    assert controls["status"] == Status.ADVERSE.value
    assert controls["severity"] == "critical"
