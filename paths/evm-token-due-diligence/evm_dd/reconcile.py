"""Conservation arithmetic for treasuries, fee wallets and distributions.

Every balance question in this pack is answered with one identity, in
exact integers, in the asset's own base units:

    opening + inflows + adjustments = outflows + closing + unexplained

The residual is never silently absorbed. It is computed, bounded, and
reported — because "the vault holds 41 ETH and 39 of them came from LP
fees" is only worth saying when the other two are accounted for or
explicitly declared unexplained.

Floats never appear. Base units are integers; a rounded balance is a
different number than the balance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class Direction(str, Enum):
    IN = "in"
    OUT = "out"


class FlowKind(str, Enum):
    """Why value moved. Kinds change how a flow is counted."""

    TRANSFER = "transfer"
    FEE_COLLECTION = "fee_collection"
    SWAP = "swap"
    MINT = "mint"
    BURN = "burn"
    GAS = "gas"
    BRIDGE_OUT = "bridge_out"
    BRIDGE_IN = "bridge_in"
    # A transformation is the same value in another form — wrapping ETH,
    # unwrapping WETH, a bridge's two legs seen from one ledger. Counting
    # both sides as flow is the classic way a reconciliation double-counts.
    TRANSFORMATION = "transformation"
    ADJUSTMENT = "adjustment"


class ReconcileError(ValueError):
    pass


@dataclass(frozen=True)
class Flow:
    label: str
    amount: int
    direction: Direction
    kind: FlowKind = FlowKind.TRANSFER
    tx_hash: str = ""
    counterparty: str = ""
    block_number: int | None = None
    note: str = ""
    reverted: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.amount, bool) or not isinstance(self.amount, int):
            raise ReconcileError(
                f"{self.label}: amounts are exact base-unit integers, got "
                f"{type(self.amount).__name__}"
            )
        if self.amount < 0:
            raise ReconcileError(
                f"{self.label}: use direction, not a negative amount"
            )

    @property
    def counts(self) -> bool:
        """Reverted transactions moved nothing; transformations net out."""
        return not self.reverted and self.kind is not FlowKind.TRANSFORMATION

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "amount": str(self.amount),
            "direction": self.direction.value,
            "kind": self.kind.value,
            "tx_hash": self.tx_hash,
            "counterparty": self.counterparty,
            "block_number": self.block_number,
            "counted": self.counts,
            "reverted": self.reverted,
            "note": self.note,
        }


@dataclass
class Reconciliation:
    """One asset, one holder, one window, closed to an explicit residual."""

    asset: str
    holder: str
    decimals: int
    opening: int
    closing: int
    from_block: int
    to_block: int
    flows: list[Flow] = field(default_factory=list)
    tolerance: int = 0

    def add(self, flow: Flow) -> Flow:
        self.flows.append(flow)
        return flow

    @property
    def inflows(self) -> int:
        return sum(
            flow.amount
            for flow in self.flows
            if flow.counts and flow.direction is Direction.IN
        )

    @property
    def outflows(self) -> int:
        return sum(
            flow.amount
            for flow in self.flows
            if flow.counts and flow.direction is Direction.OUT
        )

    @property
    def unexplained(self) -> int:
        """closing - (opening + inflows - outflows). Signed, exact."""
        return self.closing - (self.opening + self.inflows - self.outflows)

    @property
    def turnover(self) -> int:
        return max(self.inflows + self.outflows, abs(self.closing - self.opening), 1)

    @property
    def unexplained_bps(self) -> int:
        return abs(self.unexplained) * 10_000 // self.turnover

    @property
    def is_closed(self) -> bool:
        return abs(self.unexplained) <= self.tolerance

    def by_kind(self, kind: FlowKind, direction: Direction | None = None) -> int:
        return sum(
            flow.amount
            for flow in self.flows
            if flow.counts
            and flow.kind is kind
            and (direction is None or flow.direction is direction)
        )

    def attributable_fraction_bps(self, kind: FlowKind) -> int | None:
        """Share of *inflow* attributable to one kind, in basis points.

        Answers "did this balance come from LP fees?" without overstating:
        the denominator is inflow actually observed, and ``None`` means
        there was no inflow to attribute.
        """
        total_in = self.inflows
        if total_in <= 0:
            return None
        return self.by_kind(kind, Direction.IN) * 10_000 // total_in

    def defects(self) -> list[str]:
        problems: list[str] = []
        if self.opening < 0 or self.closing < 0:
            problems.append("balances cannot be negative")
        if self.to_block < self.from_block:
            problems.append(f"window runs backwards: {self.from_block}→{self.to_block}")
        if not self.is_closed:
            problems.append(
                f"unexplained residual {self.unexplained} base units "
                f"({self.unexplained_bps} bps of turnover) exceeds the stated "
                f"tolerance of {self.tolerance}"
            )
        transformations = [f for f in self.flows if f.kind is FlowKind.TRANSFORMATION]
        paired_in = sum(f.amount for f in transformations if f.direction is Direction.IN)
        paired_out = sum(
            f.amount for f in transformations if f.direction is Direction.OUT
        )
        if transformations and paired_in != paired_out:
            problems.append(
                f"transformations do not net out ({paired_in} in vs "
                f"{paired_out} out) — one leg is missing, or a leg that "
                "crossed the boundary is mislabelled as a transformation"
            )
        return problems

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset": self.asset,
            "holder": self.holder,
            "decimals": self.decimals,
            "window": {"from_block": self.from_block, "to_block": self.to_block},
            "opening": str(self.opening),
            "inflows": str(self.inflows),
            "outflows": str(self.outflows),
            "closing": str(self.closing),
            "unexplained": str(self.unexplained),
            "unexplained_bps_of_turnover": self.unexplained_bps,
            "tolerance": str(self.tolerance),
            "closed": self.is_closed,
            "identity": "opening + inflows = outflows + closing + unexplained",
            "flows": [flow.to_dict() for flow in self.flows],
            "defects": self.defects(),
        }


@dataclass(frozen=True)
class Payment:
    recipient: str
    amount: int
    tx_hash: str = ""
    epoch: str = ""


@dataclass
class DistributionAudit:
    """Conservation over a rewards or airdrop distribution.

    Deliberately does *not* assume distributions must be smaller than
    purchases: prefunding, donations, carryover and minting are all real,
    so the funded amount is an input, not an inference.
    """

    funded: int
    entitlements: Mapping[str, int]
    payments: list[Payment] = field(default_factory=list)
    retained_inventory: int = 0
    cumulative_caps: Mapping[str, int] = field(default_factory=dict)

    @property
    def paid_total(self) -> int:
        return sum(payment.amount for payment in self.payments)

    @property
    def entitled_total(self) -> int:
        return sum(self.entitlements.values())

    @property
    def paid_by_recipient(self) -> dict[str, int]:
        totals: dict[str, int] = {}
        for payment in self.payments:
            key = payment.recipient.lower()
            totals[key] = totals.get(key, 0) + payment.amount
        return totals

    def duplicate_payments(self) -> list[str]:
        seen: dict[tuple[str, str], int] = {}
        duplicates: list[str] = []
        for payment in self.payments:
            if not payment.tx_hash:
                continue
            key = (payment.recipient.lower(), payment.epoch)
            seen[key] = seen.get(key, 0) + 1
            if seen[key] == 2:
                duplicates.append(
                    f"{payment.recipient} paid more than once for epoch "
                    f"{payment.epoch or '<unlabelled>'}"
                )
        return duplicates

    def over_cap(self) -> list[str]:
        problems: list[str] = []
        paid = self.paid_by_recipient
        for recipient, cap in self.cumulative_caps.items():
            total = paid.get(recipient.lower(), 0)
            if total > cap:
                problems.append(
                    f"{recipient} received {total} against a cumulative cap of {cap}"
                )
        return problems

    def unpaid(self) -> dict[str, int]:
        paid = self.paid_by_recipient
        return {
            recipient: amount - paid.get(recipient.lower(), 0)
            for recipient, amount in self.entitlements.items()
            if amount - paid.get(recipient.lower(), 0) > 0
        }

    def overpaid(self) -> dict[str, int]:
        entitled = {key.lower(): value for key, value in self.entitlements.items()}
        return {
            recipient: amount - entitled.get(recipient, 0)
            for recipient, amount in self.paid_by_recipient.items()
            if amount - entitled.get(recipient, 0) > 0
        }

    @property
    def unfunded_gap(self) -> int:
        """How much of the outstanding entitlement is not covered by funds."""
        outstanding = sum(self.unpaid().values())
        available = self.funded - self.paid_total + self.retained_inventory
        return max(outstanding - available, 0)

    def defects(self) -> list[str]:
        problems: list[str] = []
        if self.paid_total > self.funded + self.retained_inventory:
            problems.append(
                f"paid {self.paid_total} against funding of {self.funded} "
                f"(+{self.retained_inventory} retained): the shortfall must be "
                "explained by minting, donation or carryover, or the payment "
                "set is incomplete"
            )
        problems.extend(self.duplicate_payments())
        problems.extend(self.over_cap())
        if self.unfunded_gap:
            problems.append(
                f"outstanding entitlements exceed available funds by "
                f"{self.unfunded_gap} base units"
            )
        overpaid = self.overpaid()
        if overpaid:
            problems.append(
                "paid above entitlement: "
                + ", ".join(f"{key} (+{value})" for key, value in sorted(overpaid.items()))
            )
        return problems

    def to_dict(self) -> dict[str, Any]:
        return {
            "funded": str(self.funded),
            "entitled_total": str(self.entitled_total),
            "paid_total": str(self.paid_total),
            "retained_inventory": str(self.retained_inventory),
            "unpaid_recipients": len(self.unpaid()),
            "unpaid_total": str(sum(self.unpaid().values())),
            "unfunded_gap": str(self.unfunded_gap),
            "duplicate_payments": self.duplicate_payments(),
            "over_cap": self.over_cap(),
            "defects": self.defects(),
        }


def format_units(amount: int, decimals: int, precision: int = 6) -> str:
    """Base units to a human string without ever going through a float."""
    if decimals <= 0:
        return str(amount)
    sign = "-" if amount < 0 else ""
    magnitude = abs(amount)
    whole, fraction = divmod(magnitude, 10**decimals)
    digits = str(fraction).rjust(decimals, "0")[:precision].rstrip("0")
    return f"{sign}{whole}" + (f".{digits}" if digits else "")
