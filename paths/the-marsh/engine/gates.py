"""The seven gates — every duck passes ALL of them or the dog won't fetch.

Enforced in code, not prompt (spec §4). Each gate returns the fiction
name of what killed the duck, so the scouting log can say exactly why.
Fiction names double as stable event fields; the narrator renders them.
"""

from __future__ import annotations

from .config import MarshConfig
from .feed import Duck

# gate id -> fiction name used in logs and narration
GATE_NAMES = {
    "liquidity": "thin water",
    "heat": "cold duck",
    "age_young": "still in the nest",
    "age_old": "flown through",
    "top10": "whale pond",
    "copycat": "decoy",
    "honeypot": "baited trap",
    "safety": "bad water",
    # engine-level refusals that read like gates in the scouting log
    "safety_unchecked": "couldn't get a good look",
    "reentry_lockout": "bad blood",
    "already_holding": "already carrying one",
}


def run_gates(duck: Duck, config: MarshConfig,
              include_safety: bool = True) -> list[str]:
    """Return the list of failed gate ids (empty = duck passed).

    With include_safety False only the gates decidable from scout data
    alone run (liquidity, heat, age); the safety-fact gates (whale
    pond, decoy, trap, bad water) need a safety_check first. A duck is
    only ever shot after passing the FULL set.
    """
    failed: list[str] = []
    if duck.liquidity_usd < config.min_liquidity_usd:
        failed.append("liquidity")
    if duck.heat < config.min_heat:
        failed.append("heat")
    if duck.age_minutes < config.age_min_minutes:
        failed.append("age_young")
    if duck.age_minutes > config.age_max_hours * 60.0:
        failed.append("age_old")
    if not include_safety:
        return failed
    if duck.top10_holders_pct >= config.max_top10_holders_pct:
        failed.append("top10")
    if duck.copycat_flag:
        failed.append("copycat")
    if duck.honeypot_flag or any(
        "red" in f.lower() or "rug" in f.lower() for f in duck.red_flags
    ):
        failed.append("honeypot")
    if not _safety_ok(duck):
        failed.append("safety")
    return failed


def _safety_ok(duck: Duck) -> bool:
    if duck.chain == "solana":
        return (
            duck.mint_authority_revoked is True
            and duck.freeze_authority_revoked is True
            and duck.token2022_clean is True
        )
    # robinhood: the sell simulation must have succeeded
    return duck.sell_simulation_ok is True
