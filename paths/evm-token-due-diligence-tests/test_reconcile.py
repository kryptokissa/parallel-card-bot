import pytest

from evm_dd.reconcile import (
    Direction,
    DistributionAudit,
    Flow,
    FlowKind,
    Payment,
    ReconcileError,
    Reconciliation,
    format_units,
)

ETH = 10**18


def _window(**kwargs):
    defaults = dict(
        asset="WETH",
        holder="0x" + "aa" * 20,
        decimals=18,
        opening=0,
        closing=0,
        from_block=1,
        to_block=100,
    )
    defaults.update(kwargs)
    return Reconciliation(**defaults)


def test_the_identity_closes_when_every_flow_is_accounted_for():
    window = _window(closing=41 * ETH)
    window.add(Flow("fee collect", 39 * ETH, Direction.IN, FlowKind.FEE_COLLECTION))
    window.add(Flow("seed transfer", 2 * ETH, Direction.IN))
    assert window.unexplained == 0
    assert window.is_closed
    assert window.defects() == []


def test_an_unexplained_residual_is_reported_not_absorbed():
    window = _window(closing=50 * ETH)
    window.add(Flow("fee collect", 39 * ETH, Direction.IN, FlowKind.FEE_COLLECTION))
    assert window.unexplained == 11 * ETH
    assert not window.is_closed
    assert any("unexplained residual" in problem for problem in window.defects())


def test_fee_origin_is_a_bounded_share_of_observed_inflow():
    window = _window(closing=41 * ETH)
    window.add(Flow("fee collect", 39 * ETH, Direction.IN, FlowKind.FEE_COLLECTION))
    window.add(Flow("seed transfer", 2 * ETH, Direction.IN))
    assert window.attributable_fraction_bps(FlowKind.FEE_COLLECTION) == 9512
    assert _window().attributable_fraction_bps(FlowKind.FEE_COLLECTION) is None


def test_reverted_transactions_move_nothing():
    window = _window(closing=ETH)
    window.add(Flow("credited", ETH, Direction.IN))
    window.add(Flow("reverted attempt", 5 * ETH, Direction.IN, reverted=True))
    assert window.inflows == ETH
    assert window.is_closed


def test_transformations_must_have_both_legs_or_they_are_not_transformations():
    window = _window()
    window.add(Flow("wrap out", ETH, Direction.OUT, FlowKind.TRANSFORMATION))
    assert any("do not net out" in problem for problem in window.defects())
    window.add(Flow("wrap in", ETH, Direction.IN, FlowKind.TRANSFORMATION))
    assert window.defects() == []
    # And a netted transformation is not counted as flow on either side.
    assert window.inflows == 0 and window.outflows == 0


def test_amounts_are_exact_integers_and_direction_carries_the_sign():
    with pytest.raises(ReconcileError):
        Flow("float", 1.5, Direction.IN)  # type: ignore[arg-type]
    with pytest.raises(ReconcileError):
        Flow("negative", -1, Direction.IN)


def test_units_are_formatted_without_touching_a_float():
    assert format_units(41 * ETH, 18) == "41"
    assert format_units(1234567890123456789, 18) == "1.234567"
    assert format_units(1, 18) == "0"
    assert format_units(-(3 * ETH) // 2, 18).startswith("-1.5")


def test_a_distribution_that_pays_more_than_it_was_funded_is_flagged():
    audit = DistributionAudit(
        funded=100,
        entitlements={"0xa": 60, "0xb": 50},
        payments=[Payment("0xa", 60, "0x1", "e1"), Payment("0xa", 60, "0x2", "e1")],
    )
    problems = audit.defects()
    assert any("paid 120 against funding" in problem for problem in problems)
    assert any("more than once" in problem for problem in problems)
    assert any("above entitlement" in problem for problem in problems)


def test_prefunding_means_paying_more_than_purchases_is_not_itself_a_defect():
    # Distribution larger than any observed buy volume is normal when the
    # pool was prefunded, donated to, or carried over.
    audit = DistributionAudit(funded=1_000, entitlements={"0xa": 900}, payments=[Payment("0xa", 900)])
    assert audit.defects() == []
    assert audit.unfunded_gap == 0


def test_an_unfunded_backlog_is_measured_rather_than_assumed_away():
    audit = DistributionAudit(funded=100, entitlements={"0xa": 500}, payments=[Payment("0xa", 100)])
    assert audit.unpaid() == {"0xa": 400}
    # All funding is spent, so the whole 400 outstanding is uncovered.
    assert audit.unfunded_gap == 400
    assert any("exceed available funds" in problem for problem in audit.defects())


def test_a_cumulative_cap_breach_is_detected():
    audit = DistributionAudit(
        funded=1_000,
        entitlements={"0xa": 800},
        payments=[Payment("0xa", 500, "0x1", "e1"), Payment("0xa", 300, "0x2", "e2")],
        cumulative_caps={"0xa": 600},
    )
    assert any("cumulative cap" in problem for problem in audit.defects())


def test_the_worked_flows_file_closes_and_exercises_the_awkward_cases():
    """The flows file the README reproduces, run through the real CLI.

    It carries a netted wrap and a reverted claim on purpose: both are
    common ways a hand-built reconciliation ends up double-counting.
    """
    import json
    import subprocess
    import sys
    from pathlib import Path

    here = Path(__file__).resolve().parent
    pack_root = here.parent / "evm-token-due-diligence"
    fixture = here / "fixtures" / "example-flows.json"
    result = subprocess.run(
        [sys.executable, "scripts/main.py", "reconcile", str(fixture)],
        cwd=str(pack_root),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["closed"] is True
    assert payload["unexplained"] == "0"
    assert payload["defects"] == []
    # The reverted 5 ETH claim and both wrap legs stay out of the totals.
    assert payload["inflows"] == "42000000000000000000"
    assert payload["outflows"] == "1000000000000000000"
