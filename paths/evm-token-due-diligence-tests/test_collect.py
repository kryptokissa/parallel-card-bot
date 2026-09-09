"""End-to-end packet construction against a scripted endpoint.

No network. The transport answers exactly what a chain would, so the
pinning, batching, metadata resolution and proxy walk are all exercised —
including what happens when the endpoint lies about its chain, when a
getter reverts, and when the endpoint falls over.
"""

from __future__ import annotations

import pytest

from evm_dd.abi import encode
from evm_dd.addresses import TargetRef
from evm_dd.collect import build_packet, scan_executing_runtime
from evm_dd.evidence import Ledger
from evm_dd.keccak import selector
from evm_dd.proxy import SLOT_EIP1967_ADMIN, SLOT_EIP1967_IMPLEMENTATION
from evm_dd.rpc import ChainMismatchError, ReadOnlyRpc, RpcUnavailable
from helpers import (
    ADMIN,
    BLOCK_NUMBER,
    IMPLEMENTATION,
    TOKEN,
    ZERO_WORD,
    failing_transport,
    header,
    scripted_transport,
    word,
)

TOKEN_RUNTIME = "0x" + "63" + selector("owner()")[2:] + "f4" + "00"
IMPL_RUNTIME = "0x" + "63" + selector("mint(address,uint256)")[2:] + "00"


def _responder(*, chain_id: int = 1, metadata_reverts: set[str] | None = None, proxy: bool = True):
    metadata_reverts = metadata_reverts or set()

    def respond(method: str, params: list) -> object:
        if method == "eth_chainId":
            return hex(chain_id)
        if method == "eth_getBlockByNumber":
            return header()
        if method == "eth_getCode":
            target = str(params[0]).lower()
            if target == IMPLEMENTATION.lower():
                return IMPL_RUNTIME
            if target == ADMIN.lower():
                return "0x"
            return TOKEN_RUNTIME
        if method == "eth_getStorageAt":
            slot = params[1]
            if not proxy:
                return ZERO_WORD
            if slot == SLOT_EIP1967_IMPLEMENTATION:
                return word(IMPLEMENTATION)
            if slot == SLOT_EIP1967_ADMIN:
                return word(ADMIN)
            return ZERO_WORD
        if method == "eth_call":
            data = params[0]["data"]
            if data.startswith(selector("name()")):
                return "0x" + encode(["string"], ["Example Token"]).hex()
            if data.startswith(selector("symbol()")):
                if "symbol" in metadata_reverts:
                    return "0x"
                return "0x" + encode(["string"], ["EXAMPLE"]).hex()
            if data.startswith(selector("decimals()")):
                if "decimals" in metadata_reverts:
                    return "0x"
                return "0x" + (18).to_bytes(32, "big").hex()
            if data.startswith(selector("totalSupply()")):
                return "0x" + (10**24).to_bytes(32, "big").hex()
            if data.startswith(selector("implementation()")):
                return "0x" + bytes(12).hex() + IMPLEMENTATION[2:]
            return "0x"
        raise KeyError(method)

    return respond


def _client(**kwargs) -> ReadOnlyRpc:
    return ReadOnlyRpc(
        endpoint="https://rpc.example.org/v2/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        chain_id=1,
        transport=scripted_transport(_responder(**kwargs)),
    )


def test_a_packet_pins_the_chain_and_resolves_the_executing_code():
    ledger = Ledger()
    packet = build_packet(_client(), TargetRef(1, TOKEN), ledger=ledger, decision_question="q")
    assert packet.identity_defects() == []
    assert packet.observed_chain_id == 1
    assert packet.pin.number == BLOCK_NUMBER
    assert packet.metadata["symbol"].value == "EXAMPLE"
    assert packet.metadata["decimals"].value == 18
    assert packet.unresolved_metadata() == []
    assert packet.proxy["pattern"] == "eip1967_implementation"
    assert packet.proxy["executing_address"] == IMPLEMENTATION.lower()
    roles = {entry.role for entry in packet.scope}
    assert "implementation (executing code)" in roles
    assert "upgrade authority" in roles


def test_the_capability_scan_reads_the_implementation_not_the_proxy():
    client = _client()
    packet = build_packet(client, TargetRef(1, TOKEN))
    scan = scan_executing_runtime(client, packet)
    assert scan["scanned"] is True
    assert "supply_change" in scan["capabilities"]
    assert scan["capabilities"]["supply_change"][0]["always_material"] is True


def test_an_endpoint_answering_for_another_chain_stops_the_run():
    client = ReadOnlyRpc(
        endpoint="https://rpc.example.org",
        chain_id=1,
        transport=scripted_transport(_responder(chain_id=8453)),
    )
    with pytest.raises(ChainMismatchError) as excinfo:
        build_packet(client, TargetRef(1, TOKEN))
    assert "stop rather than substitute" in str(excinfo.value)


def test_metadata_that_does_not_answer_stays_unresolved_and_is_never_defaulted():
    packet = build_packet(
        _client(metadata_reverts={"decimals", "symbol"}), TargetRef(1, TOKEN)
    )
    assert sorted(packet.unresolved_metadata()) == ["decimals", "symbol"]
    assert packet.metadata["decimals"].value is None
    assert "returned no data" in packet.metadata["decimals"].reason
    assert packet.metadata["name"].value == "Example Token"


def test_a_non_proxy_target_scans_its_own_runtime():
    client = _client(proxy=False)
    packet = build_packet(client, TargetRef(1, TOKEN))
    assert packet.proxy["is_proxy"] is False
    # The runtime delegatecalls without a standard slot: that is recorded as
    # unresolved rather than waved through.
    assert packet.proxy["unresolved"]
    assert scan_executing_runtime(client, packet)["executing_address"].lower() == TOKEN.lower()


def test_an_endpoint_failure_becomes_a_coverage_limitation():
    client = ReadOnlyRpc(
        endpoint="https://rpc.example.org",
        chain_id=1,
        transport=failing_transport("connection reset"),
        max_retries=2,
        sleep=lambda _seconds: None,
    )
    with pytest.raises(RpcUnavailable) as excinfo:
        build_packet(client, TargetRef(1, TOKEN))
    limitation = excinfo.value.limitation
    assert "not a property of the token" in limitation.consequence
    assert client.stats.failures == 1


def test_repeated_reads_are_served_from_cache_within_a_run():
    client = _client()
    build_packet(client, TargetRef(1, TOKEN))
    before = client.stats.requests
    client.call("eth_chainId", [])
    assert client.stats.requests == before
    assert client.stats.cache_hits > 0


def test_related_addresses_get_their_runtime_status_resolved():
    """An EOA upgrade admin is a different finding from a timelock admin.

    The scripted endpoint gives the admin no code, so the packet must say
    ``no_code`` rather than leaving it unknown.
    """
    packet = build_packet(_client(), TargetRef(1, TOKEN))
    by_role = {entry.role: entry for entry in packet.scope}
    assert by_role["implementation (executing code)"].runtime_status == "code"
    assert by_role["implementation (executing code)"].runtime_hash.startswith("0x")
    admin = by_role["upgrade authority"]
    assert admin.address == ADMIN.lower()
    assert admin.runtime_status == "no_code"
    assert admin.runtime_hash == ""
