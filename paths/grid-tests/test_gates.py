"""The hard gates. Failing any one of them means no orders are planned."""

from __future__ import annotations

from engine.gates import evaluate


def _named(report, name):
    return next(r for r in report.results if r.name == name)


def test_a_sound_grid_passes_every_gate(base_config):
    report = evaluate(base_config, 100000.0)
    assert report.passed
    assert report.summary() == "all gates pass"


def test_spacing_tighter_than_fees_is_refused(base_config):
    # 40 levels across a 2% range: every completed cycle would lose money.
    doomed = base_config.with_updates(lower=99000.0, upper=101000.0, levels=40)
    report = evaluate(doomed, 100000.0)
    assert not report.passed
    assert not _named(report, "fee_coverage").passed
    assert "round trip" in _named(report, "fee_coverage").detail


def test_fee_coverage_scales_with_the_configured_multiple(base_config):
    grid = base_config.with_updates(lower=99500.0, upper=100500.0, levels=6)
    assert evaluate(grid.with_updates(min_fee_coverage=1.0), 100000.0).passed
    lax = evaluate(grid.with_updates(min_fee_coverage=1.0), 100000.0)
    strict = evaluate(grid.with_updates(min_fee_coverage=20.0), 100000.0)
    assert lax.passed and not strict.passed


def test_drawdown_gate_catches_what_the_liquidation_buffer_waves_through(base_config):
    # HL holds ~2% maintenance margin, so a grid can give back 80% of capital
    # inside its own range while still clearing the liquidation buffer 3x over.
    # The drawdown gate is the one that binds.
    risky = base_config.with_updates(lower=60000.0, upper=110000.0, leverage=5.0)
    report = evaluate(risky, 100000.0)
    assert _named(report, "liquidation_buffer").passed
    assert not _named(report, "edge_drawdown").passed
    assert not report.passed


def test_raising_the_drawdown_limit_is_an_explicit_choice(base_config):
    risky = base_config.with_updates(lower=60000.0, upper=110000.0, leverage=5.0)
    assert not evaluate(risky, 100000.0).passed
    permissive = risky.with_updates(max_edge_drawdown=0.9)
    assert evaluate(permissive, 100000.0).passed


def test_geometry_failures_are_reported_not_raised(base_config):
    # The interview relays gate text verbatim, so geometry errors have to arrive
    # as a failed gate rather than an exception.
    impossible = base_config.with_updates(lower=100000.0, upper=100010.0, levels=20)
    report = evaluate(impossible, 100000.0)
    assert not report.passed
    assert not _named(report, "geometry").passed


def test_a_grid_cannot_start_with_the_mark_outside_its_range(base_config):
    # Every rung would land on one side of the book and the grid would take the
    # breakout branch on its first step. Refuse at start instead.
    above = evaluate(base_config, 150000.0)
    assert not above.passed
    assert not _named(above, "mark_in_range").passed
    assert "already above the range" in _named(above, "mark_in_range").detail

    below = evaluate(base_config, 50000.0)
    assert not below.passed
    assert "already below the range" in _named(below, "mark_in_range").detail


def test_failure_summary_lists_only_failures(base_config):
    doomed = base_config.with_updates(lower=99000.0, upper=101000.0, levels=40)
    report = evaluate(doomed, 100000.0)
    assert "fee_coverage" in report.summary()
    assert len(report.failures) < len(report.results)
