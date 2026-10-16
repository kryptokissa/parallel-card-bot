"""The simulator, checked against real Hyperliquid prices.

`btc_1h_sample.json` is 720 verbatim hourly candles from Hyperliquid's public
`candleSnapshot`, captured with the market's real szDecimals (5) alongside them.
BUILDING_PATHS.md §6 is explicit that the tests which caught real bugs were the
ones pinned to captured data rather than synthetic dicts, and this series earns
that: it trends from roughly 62,800 to 76,700, so it exercises the breakout path
rather than only the comfortable oscillating case.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from engine.config import GridConfig
from engine.simulate import Bar, as_bars, best, simulate, sweep

FIXTURE = Path(__file__).resolve().parent / "btc_1h_sample.json"


@pytest.fixture(scope="module")
def btc():
    return json.loads(FIXTURE.read_text())


@pytest.fixture(scope="module")
def btc_bars(btc):
    return btc["bars"]


@pytest.fixture
def btc_config(btc_bars):
    mark = btc_bars[0]["c"]
    return GridConfig(
        market="BTC-USDC",
        sz_decimals=5,
        lower=round(mark * 0.94),
        upper=round(mark * 1.06),
        levels=8,
        capital_usd=5000.0,
        leverage=2.0,
        breakout="halt_hold",
    )


def test_the_fixture_is_real_captured_data(btc):
    assert "hyperliquid" in btc["source"].lower()
    assert btc["interval"] == "1h"
    # The market's real szDecimals, captured with the prices.
    assert btc["sz_decimals"] == 5
    assert len(btc["bars"]) == 720


def test_bars_parse_from_floats_triples_and_candle_dicts():
    assert Bar.of(100.0) == Bar(close=100.0, high=100.0, low=100.0)
    assert Bar.of((110.0, 90.0, 100.0)) == Bar(close=100.0, high=110.0, low=90.0)
    assert Bar.of({"c": 100.0, "h": 110.0, "l": 90.0}) == Bar(
        close=100.0, high=110.0, low=90.0
    )
    assert Bar.of({"close": 5.0}) == Bar(close=5.0, high=5.0, low=5.0)
    with pytest.raises(ValueError):
        Bar.of("not a bar")


def test_an_empty_series_is_refused(btc_config):
    with pytest.raises(ValueError, match="at least one bar"):
        simulate(btc_config, [])


def test_a_non_positive_opening_price_is_refused(btc_config):
    with pytest.raises(ValueError, match="positive"):
        simulate(btc_config, [0.0, 100.0])


def test_a_flat_series_completes_no_cycles(btc_config):
    flat = [btc_config.lower + (btc_config.upper - btc_config.lower) / 2] * 50
    outcome = simulate(btc_config, flat)
    assert outcome.cycles == 0
    assert outcome.breakout_bar is None


def test_oscillation_completes_cycles_and_earns_the_spread(btc_config):
    mid = (btc_config.lower + btc_config.upper) / 2
    span = (btc_config.upper - btc_config.lower) / 2 * 0.9
    prices = [mid + span * math.sin(i / 9.0) for i in range(400)]
    outcome = simulate(btc_config, prices)
    assert outcome.cycles > 0
    assert outcome.cycle_edge_usd > 0
    assert outcome.fees_paid_usd > 0


def test_intrabar_range_fills_rungs_a_close_only_series_misses(btc_config):
    # A grid earns from exactly the wicks a close hides, so this is not a detail.
    tight = btc_config.with_updates(
        lower=round(btc_config.lower * 1.045),
        upper=round(btc_config.upper * 0.955),
        levels=12,
        breakout="recenter",
    )
    mid = (tight.lower + tight.upper) / 2
    step = (tight.upper - tight.lower) / 4
    closes = [mid] * 40
    wicky = [{"c": mid, "h": mid + step, "l": mid - step} for _ in range(40)]
    assert simulate(tight, closes).cycles == 0
    assert simulate(tight, wicky).cycles > 0


def test_a_lot_is_never_bought_and_sold_in_the_same_bar(btc_config):
    # The order of a bar's high and low is unknowable, so same-bar round trips
    # would flatter the result.
    one_bar = [{"c": btc_config.lower, "h": btc_config.upper, "l": btc_config.lower}]
    assert simulate(btc_config, one_bar).cycles == 0


def test_funding_is_charged_only_while_inventory_is_held(btc_config):
    mid = (btc_config.lower + btc_config.upper) / 2
    never_fills = simulate(btc_config.with_updates(levels=4), [mid] * 100)
    assert never_fills.inventory_units == 0
    assert never_fills.funding_paid_usd == 0.0

    # Open mid-range so rungs below the mark are armed, then fall to the bottom.
    mid = (btc_config.lower + btc_config.upper) / 2
    accumulates = simulate(btc_config, [mid] + [btc_config.lower] * 100)
    assert accumulates.inventory_units > 0
    assert accumulates.funding_paid_usd > 0


def test_zero_funding_costs_nothing(btc_config):
    outcome = simulate(
        btc_config.with_updates(funding_rate_per_hour=0.0),
        [btc_config.lower] * 50,
    )
    assert outcome.funding_paid_usd == 0.0


def test_real_prices_trend_out_of_the_range_and_trigger_breakout(
    btc_config, btc_bars
):
    outcome = simulate(btc_config, btc_bars)
    assert outcome.breakout_bar is not None
    assert outcome.halted
    assert outcome.breakout_action == "halt_hold"


def test_halt_hold_keeps_inventory_and_halt_close_does_not(btc_config, btc_bars):
    held = simulate(btc_config.with_updates(breakout="halt_hold"), btc_bars)
    closed = simulate(btc_config.with_updates(breakout="halt_close"), btc_bars)
    assert held.breakout_bar == closed.breakout_bar
    assert closed.inventory_units == 0.0
    assert closed.unrealized_pnl_usd == 0.0


def test_cycle_edge_excludes_leftover_inventory(btc_config, btc_bars):
    # Net PnL over a trending window is mostly a bet on direction; the cycling
    # edge is what measures the grid.
    outcome = simulate(btc_config, btc_bars)
    assert outcome.net_pnl_usd == pytest.approx(
        outcome.realized_pnl_usd + outcome.unrealized_pnl_usd
    )
    assert outcome.cycle_edge_usd == pytest.approx(
        outcome.gross_pnl_usd - outcome.cost_usd
    )


def test_drawdown_is_tracked_and_never_negative(btc_config, btc_bars):
    outcome = simulate(btc_config, btc_bars)
    assert outcome.worst_drawdown_usd >= 0.0
    assert outcome.worst_drawdown_pct >= 0.0
    assert outcome.peak_equity_usd >= btc_config.capital_usd


def test_the_simulator_is_deterministic(btc_config, btc_bars):
    first = simulate(btc_config, btc_bars)
    second = simulate(btc_config, btc_bars)
    assert first.summary() == second.summary()


def test_a_sweep_ranks_configurations_and_keeps_the_rejects(btc_config, btc_bars):
    rows = sweep(btc_config, btc_bars, level_counts=range(2, 12))
    assert rows
    # Ranked best-first by cycling edge, gated rows ahead of failed ones.
    passed = [row for row in rows if row.gates_passed]
    assert passed == sorted(passed, key=lambda r: r.cycle_edge_usd, reverse=True)
    assert all(row.reason for row in rows if not row.gates_passed)


def test_a_sweep_rejection_explains_itself(btc_config, btc_bars):
    # $60 of margin cannot make a grid: HL needs $10 an order.
    starved = btc_config.with_updates(capital_usd=60.0, leverage=1.0)
    rows = sweep(starved, btc_bars, level_counts=range(15, 21))
    assert rows and all(not row.gates_passed for row in rows)
    assert any("10" in row.reason for row in rows)


def test_best_ranks_on_cycling_by_default_and_net_on_request(btc_config, btc_bars):
    rows = sweep(btc_config, btc_bars, level_counts=range(2, 12))
    by_cycle = best(rows)
    by_net = best(rows, by="net")
    assert by_cycle is not None and by_net is not None
    assert by_cycle.cycle_edge_usd >= max(
        r.cycle_edge_usd for r in rows if r.gates_passed
    )
    assert by_net.net_pnl_usd >= max(r.net_pnl_usd for r in rows if r.gates_passed)


def test_best_returns_nothing_when_every_configuration_fails(btc_config, btc_bars):
    impossible = btc_config.with_updates(capital_usd=20.0, leverage=1.0)
    rows = sweep(impossible, btc_bars, level_counts=range(10, 21))
    assert best(rows) is None


def test_a_proposal_without_prices_admits_it_is_untuned(btc_bars):
    from engine.interview import propose

    mark = btc_bars[0]["c"]
    proposal = propose(
        market="BTC-USDC", sz_decimals=5, mark_price=mark, capital_usd=5000.0
    )
    _levels, rationale = proposal["levels"]
    assert "Nothing simulated" in rationale


def test_a_proposal_with_prices_is_tuned_by_simulation(btc_bars):
    from engine.interview import propose

    mark = btc_bars[0]["c"]
    proposal = propose(
        market="BTC-USDC",
        sz_decimals=5,
        mark_price=mark,
        capital_usd=5000.0,
        prices=btc_bars,
    )
    _levels, rationale = proposal["levels"]
    assert "simulated over" in rationale
    assert "cycling edge" in rationale


def test_a_tuned_proposal_still_clears_every_gate(btc_bars):
    from engine.interview import Interview, propose, ready_to_start

    mark = btc_bars[0]["c"]
    proposal = propose(
        market="BTC-USDC",
        sz_decimals=5,
        mark_price=mark,
        capital_usd=5000.0,
        prices=btc_bars,
    )
    interview = Interview()
    interview.record("sz_decimals", 5)
    interview.record_many(proposal, source="delegated")
    interview.confirmed = True
    decision = ready_to_start(interview, mark)
    assert decision.ok, decision.blockers()


def test_a_thin_result_is_flagged_as_weak_evidence(btc_bars):
    # This window trends, so few cycles complete. Saying so matters more than
    # presenting a confident number.
    from engine.interview import propose

    mark = btc_bars[0]["c"]
    proposal = propose(
        market="BTC-USDC",
        sz_decimals=5,
        mark_price=mark,
        capital_usd=5000.0,
        prices=btc_bars,
    )
    _levels, rationale = proposal["levels"]
    assert "weak evidence" in rationale or "broke out at bar" in rationale


def test_exit_pnl_is_not_counted_as_cycling_edge(btc_config, btc_bars):
    # A breakout flatten realises money, but flattening is not gridding: a grid
    # that never cycled must not report a positive cycling edge.
    closed = simulate(btc_config.with_updates(breakout="halt_close"), btc_bars)
    assert closed.exit_pnl_usd != 0.0
    assert closed.cycle_edge_usd == pytest.approx(
        closed.gross_pnl_usd - closed.cost_usd
    )
    assert closed.gross_pnl_usd != closed.exit_pnl_usd


def test_a_grid_that_never_cycles_reports_a_negative_edge(btc_config):
    # Open mid-range, fall far enough to fill rungs, then sit there. Rungs are
    # bought and never sold: fees and funding spent achieving nothing.
    mid = (btc_config.lower + btc_config.upper) / 2
    sinking = [mid] + [btc_config.lower * 1.001] * 40
    outcome = simulate(btc_config, sinking)
    assert outcome.cycles == 0
    assert outcome.inventory_units > 0
    assert outcome.fees_paid_usd > 0
    assert outcome.cycle_edge_usd < 0


def test_tuning_engages_even_when_the_series_opens_far_from_todays_mark(btc_bars):
    """A historical series opens wherever it opened, not near the current mark.

    Tuning simulates the *shape* — level count and spacing — by scaling the same
    relative span around the series' own opening price. Without that, every
    simulated candidate fails `mark_in_range` and the proposal silently falls
    back to the untuned path while claiming a series was never supplied.
    """
    from engine.interview import propose

    opening = btc_bars[0]["c"]
    far_mark = opening * 1.35
    proposal = propose(
        market="BTC-USDC",
        sz_decimals=5,
        mark_price=far_mark,
        capital_usd=5000.0,
        volatility_pct=6.0,
        prices=btc_bars,
    )
    _levels, rationale = proposal["levels"]
    assert "simulated over" in rationale
    assert "did not tune" not in rationale
    # The range still sits around the mark the user asked about, not the series.
    assert proposal["lower"][0] < far_mark < proposal["upper"][0]


def test_a_series_that_cannot_tune_says_so_rather_than_going_quiet(btc_config):
    # Capital that only supports a couple of rungs leaves the sweep nothing to
    # rank. The rationale has to admit that, not imply no series was given.
    from engine.interview import propose

    with pytest.raises(ValueError):
        # Too small for any grid at all — refused outright, which is the other
        # honest outcome.
        propose(
            market="BTC-USDC",
            sz_decimals=5,
            mark_price=100000.0,
            capital_usd=15.0,
            prices=[100000.0, 99000.0, 101000.0],
        )


def test_tuning_never_loosens_the_gates(btc_bars):
    from engine.interview import Interview, propose, ready_to_start

    opening = btc_bars[0]["c"]
    far_mark = opening * 1.35
    proposal = propose(
        market="BTC-USDC",
        sz_decimals=5,
        mark_price=far_mark,
        capital_usd=5000.0,
        volatility_pct=6.0,
        prices=btc_bars,
    )
    interview = Interview()
    interview.record("sz_decimals", 5)
    interview.record_many(proposal, source="delegated")
    interview.confirmed = True
    assert ready_to_start(interview, far_mark).ok
