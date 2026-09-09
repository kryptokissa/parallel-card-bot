"""Each worked example must actually behave the way it claims to.

These tests are what stop the examples from drifting into decoration: the
locked-liquidity case must still fail on the side pool, the RPC failure
must still refuse to become a finding, and none of them may ever present
themselves as live observations.
"""

from __future__ import annotations

from evm_dd.evidence import Status
from evm_dd.examples import EXAMPLES, SYNTHETIC_NOTICE, build


def _rating(report: dict, dimension: str) -> dict:
    return next(item for item in report["ratings"] if item["dimension"] == dimension)


def test_every_example_is_labelled_synthetic_and_is_internally_sound():
    for name in EXAMPLES:
        report = build(name)
        assert report["synthetic"] is True
        assert report["synthetic_notice"] == SYNTHETIC_NOTICE
        assert report["self_defects"] == [], (name, report["self_defects"])
        assert report["verdict"]["statement"]


def test_locked_canonical_liquidity_does_not_clear_the_side_pool():
    report = build("locked-canonical-removable-side-pool")
    assert _rating(report, "canonical_lp_custody")["status"] == Status.CLEAR.value
    assert _rating(report, "side_pool_removal_risk")["status"] == Status.ADVERSE.value
    # The clean canonical result must not carry the verdict.
    assert report["verdict"]["call"] == "NO-GO"
    assert "side_pool_removal_risk" in report["verdict"]["statement"]


def test_fixed_supply_still_fails_on_holder_sized_exit():
    report = build("fixed-supply-severe-exit-degradation")
    assert _rating(report, "token_controls")["status"] == Status.CLEAR.value
    assert _rating(report, "sellability_and_depth")["status"] == Status.ADVERSE.value
    depth = next(item for item in report["findings"] if item["finding_id"] == "SD-1")
    # A quote is never presented as a realised exit.
    assert "not realised exits" in depth["coverage"]
    assert depth["confidence"] == "strong"


def test_an_immutable_token_does_not_clear_its_upgradeable_reward_layer():
    report = build("upgradeable-reward-layer")
    assert _rating(report, "token_controls")["status"] == Status.CLEAR.value
    assert _rating(report, "admin_treasury_custody")["status"] == Status.ADVERSE.value
    assert report["headline_severity"] == "critical"
    assert report["verdict"]["call"] == "NO-GO"


def test_sell_and_rebuy_is_redistribution_not_a_proven_cash_out():
    report = build("launch-wallets-sold-and-rebought")
    finding = next(item for item in report["findings"] if item["finding_id"] == "LI-1")
    assert "market-mediated redistribution" in finding["notes"]
    assert "not called profit" in finding["notes"] or "not called profit" in finding["notes"]
    # The cohort must be defined before it is measured.
    assert "Cohort defined as" in finding["coverage"]
    # Common ownership is explicitly held open.
    assert any("common ownership" in item for item in finding["alternatives"])


def test_a_vault_of_claims_is_not_proven_backing():
    report = build("vault-of-synthetic-claims")
    holdings = next(item for item in report["findings"] if item["finding_id"] == "BK-1")
    redemption = next(item for item in report["findings"] if item["finding_id"] == "BK-2")
    assert holdings["status"] == Status.ADVERSE.value
    assert redemption["status"] == Status.UNKNOWN.value
    assert redemption["confidence"] == "unknown"
    assert "balance delta" in redemption["notes"]


def test_an_rpc_failure_is_a_coverage_limitation_and_never_a_pass():
    report = build("rpc-failure-is-a-coverage-limit")
    assert report["coverage_limitations"], "the failure must be recorded as coverage"
    limitation = report["coverage_limitations"][0]
    assert "missing trie node" in limitation["reason"]
    assert "unmeasured" in limitation["consequence"]
    rating = _rating(report, "current_concentration")
    assert rating["status"] == Status.UNKNOWN.value
    assert report["verdict"]["call"] == "INSUFFICIENT EVIDENCE"
    # No finding claims anything about the token because of the node's state.
    for finding in report["findings"]:
        assert finding["status"] != Status.CLEAR.value


def test_unrated_surfaces_are_reported_as_not_checked_rather_than_omitted():
    report = build("fixed-supply-severe-exit-degradation")
    statuses = {item["dimension"]: item["status"] for item in report["ratings"]}
    assert len(statuses) == 11
    assert statuses["launch_integrity"] == Status.NOT_CHECKED.value
