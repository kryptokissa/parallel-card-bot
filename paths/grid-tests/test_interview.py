"""The interview: completeness, delegation, confirmation, and proposals."""

from __future__ import annotations

import pytest

from engine.gates import evaluate
from engine.interview import (
    QUESTIONS,
    Interview,
    propose,
    ready_to_start,
)


def _complete_answers() -> dict[str, object]:
    return {
        "market": "BTC-USDC",
        "sz_decimals": 5,
        "lower": 94000.0,
        "upper": 106000.0,
        "levels": 6,
        "capital_usd": 5000.0,
        "leverage": 2.0,
        "breakout": "halt_hold",
    }


def test_every_question_is_delegable_and_breakout_has_no_default():
    breakout = next(q for q in QUESTIONS if q.key == "breakout")
    assert not breakout.has_default
    assert set(breakout.options) == {"halt_hold", "halt_close", "recenter"}


def test_spacing_and_leverage_are_the_only_defaulted_questions():
    defaulted = {q.key for q in QUESTIONS if q.has_default}
    assert defaulted == {"spacing", "leverage"}


def test_an_unanswered_breakout_blocks_the_start():
    interview = Interview()
    answers = _complete_answers()
    del answers["breakout"]
    interview.record_many(answers)
    decision = ready_to_start(interview, 100000.0)
    assert not decision.ok
    assert "breakout" in decision.reason


def test_a_complete_interview_starts():
    interview = Interview()
    interview.record_many(_complete_answers())
    decision = ready_to_start(interview, 100000.0)
    assert decision.ok
    assert decision.gates is not None and decision.gates.passed


def test_a_delegated_answer_requires_confirmation():
    interview = Interview()
    interview.record_many(_complete_answers(), source="delegated")
    assert interview.requires_confirmation
    decision = ready_to_start(interview, 100000.0)
    assert not decision.ok
    assert "confirmation" in decision.reason

    interview.confirmed = True
    assert ready_to_start(interview, 100000.0).ok


def test_answers_the_user_gave_need_no_confirmation():
    interview = Interview()
    interview.record_many(_complete_answers(), source="user")
    assert not interview.requires_confirmation
    assert ready_to_start(interview, 100000.0).ok


def test_provenance_records_who_chose_what():
    interview = Interview()
    interview.record_many(_complete_answers())
    interview.record("breakout", "recenter", "delegated", "safest given the range")
    provenance = interview.provenance()
    assert provenance["breakout"] == "delegated"
    assert provenance["market"] == "user"
    assert interview.delegated_keys == ["breakout"]


def test_delegation_does_not_relax_the_gates():
    # A delegated answer goes through exactly the same hard gates.
    interview = Interview()
    answers = _complete_answers()
    answers.update({"lower": 99000.0, "upper": 101000.0, "levels": 40})
    interview.record_many(answers, source="delegated")
    interview.confirmed = True
    decision = ready_to_start(interview, 100000.0)
    assert not decision.ok
    assert any("cost_coverage" in b for b in decision.blockers())


def test_clamped_leverage_still_starts_but_reports_the_clamp():
    interview = Interview()
    answers = _complete_answers()
    answers["leverage"] = 50.0
    interview.record_many(answers)
    decision = ready_to_start(interview, 100000.0)
    assert decision.resolved is not None
    assert decision.resolved.config.leverage == 5.0
    assert decision.resolved.adjustments


def test_unknown_keys_are_not_silently_accepted():
    interview = Interview()
    with pytest.raises(KeyError):
        interview.record("sneaky_override", 1)


def test_a_proposal_passes_every_gate_it_will_later_be_held_to():
    proposal = propose(
        market="ETH-USDC",
        sz_decimals=4,
        mark_price=3000.0,
        capital_usd=2000.0,
        volatility_pct=5.0,
    )
    interview = Interview()
    interview.record("sz_decimals", 4)
    interview.record_many(proposal, source="delegated")
    interview.confirmed = True
    decision = ready_to_start(interview, 3000.0)
    assert decision.ok, decision.blockers()


def test_every_proposed_value_carries_its_reasoning():
    proposal = propose(
        market="ETH-USDC", sz_decimals=4, mark_price=3000.0, capital_usd=2000.0
    )
    for key, (_value, rationale) in proposal.items():
        assert rationale.strip(), f"{key} was proposed with no reasoning"


def test_a_proposed_breakout_is_presented_as_a_choice_not_a_default():
    proposal = propose(
        market="ETH-USDC", sz_decimals=4, mark_price=3000.0, capital_usd=2000.0
    )
    _value, rationale = proposal["breakout"]
    assert "not a default" in rationale
    assert "halt_hold" in rationale and "recenter" in rationale


def test_proposed_bounds_are_already_on_the_venue_grid():
    from engine.ticks import is_on_grid

    proposal = propose(
        market="ETH-USDC", sz_decimals=4, mark_price=3000.0, capital_usd=2000.0
    )
    assert is_on_grid(proposal["lower"][0], 4)
    assert is_on_grid(proposal["upper"][0], 4)


def test_capital_too_small_for_any_grid_is_refused_clearly():
    with pytest.raises(ValueError, match="clears the hard gates"):
        propose(
            market="BTC-USDC", sz_decimals=5, mark_price=100000.0, capital_usd=15.0
        )


def test_a_zero_mark_is_refused_before_anything_is_proposed():
    with pytest.raises(ValueError, match="mark_price"):
        propose(
            market="BTC-USDC", sz_decimals=5, mark_price=0.0, capital_usd=5000.0
        )
