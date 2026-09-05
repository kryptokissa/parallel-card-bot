"""The validator must reject the four ways a report goes quietly wrong.

Each case below is a report that looks entirely plausible — well
formatted, confident, internally fluent — and is about the wrong thing, or
claims more than it checked. These are the failures the manifest exists
to catch.
"""

from __future__ import annotations

import pytest

from evm_dd.examples import fixed_supply_with_severe_exit_degradation
from evm_dd.manifest import Declarations, build_manifest, validate_manifest, validate_report


def _pair() -> tuple[dict, dict]:
    report = fixed_supply_with_severe_exit_degradation()
    report.source_id = "case-under-test"
    manifest = build_manifest(
        report.packet,
        declarations=Declarations(rpc_endpoints=["synthetic://example"]),
        report_id="case-under-test",
    )
    return manifest, report.to_dict()


def test_the_honest_pair_passes():
    manifest, report = _pair()
    assert validate_manifest(manifest).ok
    result = validate_report(manifest, report)
    assert result.ok, result.errors


# -- case 1: a same-symbol token from another chain -------------------------


def test_rejects_a_same_symbol_token_substituted_from_another_chain():
    manifest, report = _pair()
    # The user asked about chain 1. The endpoint answered for Base, where a
    # token with the same symbol lives at the same address.
    manifest["target"]["observed_chain_id"] = 8453
    result = validate_manifest(manifest)
    assert not result.ok
    assert any("different token" in error for error in result.errors)


def test_rejects_a_report_that_moved_the_target_to_another_chain():
    manifest, report = _pair()
    report["target"]["chain_id"] = 8453
    result = validate_report(manifest, report)
    assert not result.ok
    assert any("chain 8453" in error for error in result.errors)


def test_rejects_a_finding_recorded_on_an_unpinned_chain():
    manifest, report = _pair()
    report["findings"][0]["chain_id"] = 42161
    result = validate_report(manifest, report)
    assert not result.ok
    assert any("no pin in this manifest" in error for error in result.errors)


# -- case 2: a report about the wrong target -------------------------------


def test_rejects_a_report_about_a_different_address():
    manifest, report = _pair()
    report["target"]["address"] = "0x" + "de" * 20
    result = validate_report(manifest, report)
    assert not result.ok
    assert any("different address" in error for error in result.errors)


def test_rejects_a_report_built_from_a_different_target_packet():
    manifest, report = _pair()
    report["source"]["packet_digest"] = "0x" + "00" * 32
    result = validate_report(manifest, report)
    assert not result.ok
    assert any("different target packet" in error for error in result.errors)


def test_rejects_a_malformed_address_anywhere_it_matters():
    manifest, report = _pair()
    report["target"]["address"] = "0xnothex"
    assert not validate_report(manifest, report).ok

    manifest2, report2 = _pair()
    manifest2["scope"].append(
        {
            "address": "0x1234",
            "chain_id": 1,
            "role": "pool",
            "provenance": "guessed",
            "runtime_status": "code",
        }
    )
    assert not validate_manifest(manifest2).ok


# -- case 3: a fake or inconsistent block pin ------------------------------


@pytest.mark.parametrize(
    "mutation",
    [
        {"number": 0},
        {"block_hash": "0x" + "00" * 32},
        {"timestamp": 0},
        {"block_hash": "0xdeadbeef"},
    ],
)
def test_rejects_placeholder_pins(mutation):
    manifest, _ = _pair()
    manifest["pins"]["1"].update(mutation)
    assert not validate_manifest(manifest).ok


def test_rejects_a_report_pin_that_disagrees_with_the_manifest():
    manifest, report = _pair()
    report["pins"]["1"]["block_hash"] = "0x" + "99" * 32
    result = validate_report(manifest, report)
    assert not result.ok
    assert any("does not match manifest pin" in error for error in result.errors)


def test_rejects_a_pin_that_disagrees_with_the_fetched_header():
    manifest, report = _pair()
    fetched = {
        "number": hex(manifest["pins"]["1"]["number"]),
        "hash": "0x" + "77" * 32,  # the chain says something else
        "timestamp": hex(manifest["pins"]["1"]["timestamp"]),
    }
    result = validate_report(manifest, report, headers={"1": fetched})
    assert not result.ok
    assert any("disagrees with the fetched header" in error for error in result.errors)


def test_accepts_a_pin_that_matches_the_fetched_header():
    manifest, report = _pair()
    fetched = {
        "number": hex(manifest["pins"]["1"]["number"]),
        "hash": manifest["pins"]["1"]["block_hash"],
        "timestamp": hex(manifest["pins"]["1"]["timestamp"]),
    }
    assert validate_report(manifest, report, headers={"1": fetched}).ok


# -- case 4: an unknown presented as a pass --------------------------------


def test_rejects_an_unknown_check_presented_as_a_pass():
    manifest, report = _pair()
    rating = next(
        item for item in report["ratings"] if item["dimension"] == "sellability_and_depth"
    )
    rating["status"] = "clear"
    rating["check_statuses"] = ["clear", "unknown"]
    rating["coverage"] = "one route, two sizes"
    result = validate_report(manifest, report)
    assert not result.ok
    assert any("presented as a settled result" in error for error in result.errors)


def test_rejects_a_skipped_check_presented_as_a_pass():
    manifest, report = _pair()
    rating = next(item for item in report["ratings"] if item["dimension"] == "token_controls")
    rating["status"] = "clear"
    rating["check_statuses"] = ["not_checked"]
    assert not validate_report(manifest, report).ok


def test_rejects_a_favourable_rating_with_no_coverage_statement():
    manifest, report = _pair()
    rating = next(item for item in report["ratings"] if item["dimension"] == "token_controls")
    rating["coverage"] = ""
    result = validate_report(manifest, report)
    assert not result.ok
    assert any("must state its coverage" in error for error in result.errors)


# -- supporting rules ------------------------------------------------------


def test_rejects_a_material_settled_finding_with_no_evidence():
    manifest, report = _pair()
    report["findings"][0]["evidence"] = []
    result = validate_report(manifest, report)
    assert not result.ok
    assert any("cites no evidence" in error for error in result.errors)


def test_rejects_metadata_that_is_neither_resolved_nor_explicitly_unresolved():
    manifest, _ = _pair()
    del manifest["metadata"]["decimals"]
    result = validate_manifest(manifest)
    assert not result.ok
    assert any("metadata.decimals is absent" in error for error in result.errors)

    manifest2, _ = _pair()
    manifest2["metadata"]["symbol"] = {"state": "unresolved", "reason": ""}
    assert not validate_manifest(manifest2).ok


def test_rejects_a_scope_address_without_role_or_provenance():
    manifest, _ = _pair()
    manifest["scope"][0]["provenance"] = ""
    assert not validate_manifest(manifest).ok


def test_rejects_a_run_that_claims_to_have_signed_or_broadcast():
    manifest, _ = _pair()
    manifest["declarations"]["no_broadcast"] = False
    result = validate_manifest(manifest)
    assert not result.ok
    assert any("no signing path" in error for error in result.errors)


def test_rejects_fork_simulation_that_is_not_on_a_local_fork():
    manifest, _ = _pair()
    manifest["declarations"]["simulation"] = "fork"
    manifest["declarations"]["fork_endpoint_loopback"] = False
    result = validate_manifest(manifest)
    assert not result.ok
    assert any("disposable local fork" in error for error in result.errors)


def test_rejects_an_unconditional_safety_verdict():
    manifest, report = _pair()
    report["verdict"]["statement"] = "This token is safe and cannot be rugged."
    result = validate_report(manifest, report)
    assert not result.ok
    assert any("unconditional safety" in error for error in result.errors)


def test_warns_when_a_verdict_carries_no_bound():
    manifest, report = _pair()
    report["verdict"]["statement"] = "Looks fine to me."
    result = validate_report(manifest, report)
    assert any("no explicit bound" in warning for warning in result.warnings)


# -- the verdict may not be cleaner than the ratings under it ---------------


def test_rejects_a_go_verdict_over_unsettled_surfaces():
    manifest, report = _pair()
    report["verdict"]["call"] = "GO"
    report["verdict"]["statement"] = "No adverse result found at the pinned block."
    result = validate_report(manifest, report)
    assert not result.ok
    assert any("may not rest on surfaces that were never closed" in error for error in result.errors)


def test_accepts_a_go_verdict_when_every_surface_is_settled():
    manifest, report = _pair()
    for rating in report["ratings"]:
        rating["status"] = "clear"
        rating["check_statuses"] = ["clear"]
        rating["coverage"] = rating["coverage"] or "stated coverage"
    report["verdict"]["call"] = "GO"
    report["verdict"]["statement"] = (
        "No adverse result found on any rated surface at the pinned block."
    )
    result = validate_report(manifest, report)
    assert result.ok, result.errors


def test_rejects_a_report_that_does_not_pin_its_own_chain():
    manifest, report = _pair()
    report["pins"] = {}
    result = validate_report(manifest, report)
    assert not result.ok
    assert any("no pin for its own target chain" in error for error in result.errors)


def test_a_report_with_no_ratings_cannot_carry_a_favourable_call():
    manifest, report = _pair()
    report["ratings"] = []
    report["verdict"]["call"] = "GO"
    assert not validate_report(manifest, report).ok
