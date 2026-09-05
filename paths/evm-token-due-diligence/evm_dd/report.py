"""Ratings, verdict assembly and the report document.

Two rules shape this module:

*   The eleven surfaces are rated **separately**. A token can have
    immaculate code and unexitable liquidity; collapsing that into one
    number is how a diligence report becomes a score, and a score is what
    the reader was trying to get away from.
*   A critical finding is never averaged away. The verdict is driven by
    the worst material result, not by the count of clean ones.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from evm_dd.addresses import to_checksum
from evm_dd.evidence import (
    Confidence,
    Ledger,
    Status,
    weakest_confidence,
    worst_status,
)
from evm_dd.target import TargetPacket

# The rated surfaces, in reading order. Every broad report rates all of
# them; a focused answer rates the ones it touched and marks the rest
# not_checked, which is a statement, not a gap.
DIMENSIONS: tuple[tuple[str, str], ...] = (
    ("token_controls", "What privileged actors can change or take in the token itself"),
    ("canonical_lp_custody", "Who can remove principal from the canonical pool"),
    ("side_pool_removal_risk", "Liquidity outside the canonical pool, and who can pull it"),
    ("sellability_and_depth", "Whether ordinary holders can sell, and at what size"),
    ("current_concentration", "Who holds the supply now, and under what denominator"),
    ("launch_integrity", "How the launch was allocated, funded and sold"),
    ("admin_treasury_custody", "Custody of admin, treasury and reward assets"),
    ("reward_accounting", "Whether reward accounting conserves and keeps running"),
    ("utility_and_redemption", "Whether advertised rights are live and enforceable"),
    ("external_dependencies", "Oracles, bridges, keepers and other outside parts"),
    ("development_and_disclosure", "Source correspondence, controls and disclosure accuracy"),
)

DIMENSION_IDS = tuple(name for name, _ in DIMENSIONS)

SEVERITY_ORDER = ("informational", "low", "medium", "high", "critical")


def severity_rank(value: str) -> int:
    return SEVERITY_ORDER.index(value) if value in SEVERITY_ORDER else 0


@dataclass
class Rating:
    """One surface's result. Its status is derived, never asserted."""

    dimension: str
    check_statuses: list[Status] = field(default_factory=list)
    confidences: list[Confidence] = field(default_factory=list)
    severity: str = "informational"
    likelihood: str = "unassessed"
    coverage: str = ""
    time_basis: str = ""
    summary: str = ""
    finding_ids: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.dimension not in DIMENSION_IDS:
            raise ValueError(
                f"unknown dimension {self.dimension!r}; expected one of "
                f"{DIMENSION_IDS}"
            )

    @property
    def status(self) -> Status:
        return worst_status(self.check_statuses)

    @property
    def confidence(self) -> Confidence:
        return weakest_confidence(self.confidences)

    def defects(self) -> list[str]:
        problems: list[str] = []
        if self.status.is_favourable and not self.coverage.strip():
            problems.append(
                f"{self.dimension}: a favourable rating must state what it "
                "covered and what it did not"
            )
        if self.status.is_settled and not self.time_basis.strip():
            problems.append(f"{self.dimension}: settled result with no time basis")
        return problems

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "status": self.status.value,
            "confidence": self.confidence.value,
            "severity": self.severity,
            "likelihood": self.likelihood,
            "coverage": self.coverage,
            "time_basis": self.time_basis,
            "summary": self.summary,
            "check_statuses": [item.value for item in self.check_statuses],
            "finding_ids": list(self.finding_ids),
        }


@dataclass
class Verdict:
    """A direct answer to the question that was actually asked."""

    question: str
    call: str  # "GO" | "NO-GO" | "CONDITIONAL" | "INSUFFICIENT EVIDENCE"
    statement: str
    main_reasons: list[str] = field(default_factory=list)
    strongest_contrary_evidence: list[str] = field(default_factory=list)
    unresolved_questions: list[str] = field(default_factory=list)
    would_change_this: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "call": self.call,
            "statement": self.statement,
            "main_reasons": list(self.main_reasons),
            "strongest_contrary_evidence": list(self.strongest_contrary_evidence),
            "unresolved_questions": list(self.unresolved_questions),
            "would_change_this": list(self.would_change_this),
        }


def derive_verdict(
    *,
    question: str,
    requirement: str,
    ratings: Sequence[Rating],
    pin_description: str,
    required_dimensions: Sequence[str] = DIMENSION_IDS,
) -> Verdict:
    """Assemble the call from the ratings, worst-first, without averaging."""
    by_dimension = {rating.dimension: rating for rating in ratings}
    required = [by_dimension[name] for name in required_dimensions if name in by_dimension]

    adverse = [item for item in required if item.status is Status.ADVERSE]
    blocking = [item for item in adverse if severity_rank(item.severity) >= severity_rank("high")]
    unknown = [
        item
        for item in required
        if item.status in (Status.UNKNOWN, Status.NOT_CHECKED)
    ]
    missing = [name for name in required_dimensions if name not in by_dimension]

    reasons: list[str] = []
    if blocking:
        call = "NO-GO"
        statement = (
            f"NO-GO under the stated requirement ({requirement}). "
            + "; ".join(
                f"{item.dimension} is adverse at {item.severity} severity"
                for item in blocking
            )
            + f", {pin_description}."
        )
        reasons = [item.summary or item.dimension for item in blocking]
    elif adverse:
        call = "CONDITIONAL"
        statement = (
            f"Conditional under the stated requirement ({requirement}): "
            + "; ".join(f"{item.dimension} is adverse" for item in adverse)
            + f" at {'medium or lower'} severity, {pin_description}. "
            "The adverse findings are survivable only if the conditions below hold."
        )
        reasons = [item.summary or item.dimension for item in adverse]
    elif unknown or missing:
        call = "INSUFFICIENT EVIDENCE"
        open_names = [item.dimension for item in unknown] + list(missing)
        statement = (
            "Insufficient evidence for a call under the stated requirement "
            f"({requirement}): "
            + ", ".join(open_names)
            + f" could not be settled, {pin_description}. Unknown is not a pass."
        )
        reasons = [
            f"{item.dimension}: {item.coverage or 'not established'}" for item in unknown
        ] + [f"{name}: not rated" for name in missing]
    else:
        call = "GO"
        statement = (
            f"No adverse result found on any rated surface at the pinned block "
            f"({pin_description}), under the stated requirement ({requirement}) "
            "and within the coverage recorded per surface. This is a bounded "
            "result about a pinned state, not a safety guarantee."
        )
        reasons = [item.summary or item.dimension for item in required if item.summary]

    return Verdict(
        question=question,
        call=call,
        statement=statement,
        main_reasons=reasons[:6],
    )


@dataclass
class Report:
    """The document the validator checks and the applet renders."""

    packet: TargetPacket
    ledger: Ledger
    ratings: list[Rating] = field(default_factory=list)
    verdict: Verdict | None = None
    mode: str = "broad"  # "focused" | "broad" | "formal"
    source_id: str = ""
    generated_at: str = ""
    rpc_stats: Mapping[str, Any] = field(default_factory=dict)

    def rate(self, rating: Rating) -> Rating:
        self.ratings = [item for item in self.ratings if item.dimension != rating.dimension]
        self.ratings.append(rating)
        return rating

    def ordered_ratings(self) -> list[Rating]:
        index = {name: position for position, (name, _) in enumerate(DIMENSIONS)}
        return sorted(self.ratings, key=lambda item: index.get(item.dimension, 99))

    @property
    def headline_severity(self) -> str:
        adverse = [
            item.severity
            for item in self.ratings
            if item.status is Status.ADVERSE
        ]
        return max(adverse, key=severity_rank) if adverse else "informational"

    def defects(self) -> list[str]:
        problems = list(self.ledger.defects())
        for rating in self.ratings:
            problems.extend(rating.defects())
        problems.extend(self.packet.identity_defects())
        if self.verdict is None:
            problems.append("report has no verdict")
        return problems

    def to_dict(self) -> dict[str, Any]:
        digest, packet_payload = self.packet.freeze()
        pins = {str(self.packet.pin.chain_id): self.packet.pin.to_dict()}
        for chain_id, pin in self.packet.additional_pins.items():
            pins[str(chain_id)] = pin.to_dict()
        ledger_payload = self.ledger.to_dict()
        return {
            "schema": "evm-token-due-diligence/report/1",
            "mode": self.mode,
            "generated_at": self.generated_at
            or datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z"),
            "source": {"id": self.source_id, "packet_digest": digest},
            "target": {
                "chain_id": self.packet.requested.chain_id,
                "address": to_checksum(self.packet.requested.address),
                "caip10": self.packet.requested.caip10,
                "metadata": packet_payload["metadata"],
                "unresolved_metadata": packet_payload["unresolved_metadata"],
                "runtime": packet_payload["runtime"],
                "proxy": packet_payload["proxy"],
            },
            "pins": pins,
            "decision_question": self.packet.decision_question,
            "materiality": self.packet.materiality,
            "headline_severity": self.headline_severity,
            "ratings": [item.to_dict() for item in self.ordered_ratings()],
            "findings": ledger_payload["findings"],
            "coverage_limitations": ledger_payload["coverage_limitations"],
            "known_limitations": list(self.packet.known_limitations),
            "verdict": self.verdict.to_dict() if self.verdict else {},
            "rpc": dict(self.rpc_stats),
            "self_defects": self.defects(),
        }


def blank_ratings(reason: str = "not reached in this run") -> list[Rating]:
    """Every surface, explicitly unexamined. The honest starting point."""
    return [
        Rating(
            dimension=name,
            check_statuses=[Status.NOT_CHECKED],
            confidences=[Confidence.UNKNOWN],
            coverage=reason,
            summary=description,
        )
        for name, description in DIMENSIONS
    ]
