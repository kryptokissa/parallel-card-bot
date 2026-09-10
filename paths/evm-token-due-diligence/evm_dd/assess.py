"""Turning collected evidence into rated surfaces.

This is the join the pack was missing: `collect` gathers a pinned target
packet and a capability scan, and `report` renders a document, but nothing
converted the first into the second. Everything here is derived from
evidence actually in hand — no surface is rated on the strength of
something that was not read.

The rule that shapes the whole module: a surface the run did not reach is
rated ``not_checked`` and says so. It is never omitted, and never quietly
clear. A broad requirement over a run this narrow therefore lands on
INSUFFICIENT EVIDENCE, which is the honest answer, not a failure.
"""

from __future__ import annotations

from typing import Any, Mapping

from evm_dd.addresses import to_checksum
from evm_dd.evidence import (
    Confidence,
    Evidence,
    EvidenceKind,
    Finding,
    Ledger,
    Status,
)
from evm_dd.report import DIMENSION_IDS, DIMENSIONS, Rating, Report, derive_verdict
from evm_dd.selectors import ALWAYS_MATERIAL, SEVERITY_BY_CAPABILITY, describe_limits
from evm_dd.target import TargetPacket

# Capability families that make the token's own controls adverse on sight.
# Their presence is the finding; no monetary threshold applies.
CONTROL_FAMILIES = (
    "supply_change",
    "balance_rewrite",
    "upgrade",
    "arbitrary_call",
    "transfer_restriction",
    "asset_extraction",
    "fee_control",
)

# Families that describe an ordinary, permissionless surface. They are
# reported so a reader can see them, and never rated adverse: `authority`
# says roles exist, not that they are dangerous, and `holder_burn` is the
# standard ERC20Burnable pair. Reading either as privilege is how a tool
# cries wolf on the first honest token it meets.
NON_PRIVILEGED_FAMILIES = ("authority", "holder_burn", "external_dependency")

_SEVERITY_ORDER = ("informational", "low", "medium", "high", "critical")


def _worst_severity(values: list[str]) -> str:
    ranked = [v for v in values if v in _SEVERITY_ORDER]
    return max(ranked, key=_SEVERITY_ORDER.index) if ranked else "informational"


def _runtime_evidence(packet: TargetPacket, scan: Mapping[str, Any]) -> Evidence:
    executing = scan.get("executing_address") or to_checksum(packet.requested.address)
    return Evidence(
        kind=EvidenceKind.RUNTIME_BYTECODE,
        summary=(
            f"runtime of {executing}: {scan.get('runtime_size', 0)} bytes, "
            f"{scan.get('selector_count', 0)} selectors"
        ),
        query=f"eth_getCode({executing}, {packet.pin.block_tag})",
        result_digest=str(scan.get("runtime_hash") or ""),
        chain_id=packet.observed_chain_id,
        address=packet.requested.address,
        block_number=packet.pin.number,
        source=packet.endpoint_identity,
    )


def _slot_evidence(packet: TargetPacket, label: str) -> Evidence:
    return Evidence(
        kind=EvidenceKind.STORAGE_SLOT,
        summary=f"{label} at the pinned block",
        query=f"eth_getStorageAt({to_checksum(packet.requested.address)}, <{label}>, {packet.pin.block_tag})",
        chain_id=packet.observed_chain_id,
        address=packet.requested.address,
        block_number=packet.pin.number,
        source=packet.endpoint_identity,
    )


def assess_token_controls(
    packet: TargetPacket, scan: Mapping[str, Any], ledger: Ledger
) -> Rating:
    """Surface A, from the executing runtime and the proxy walk."""
    pin_basis = f"block {packet.pin.number} ({packet.pin.utc})"
    proxy = dict(packet.proxy or {})
    checks: list[Status] = []
    confidences: list[Confidence] = []
    finding_ids: list[str] = []
    severities: list[str] = []
    summaries: list[str] = []

    # --- what the deployed code exposes -------------------------------
    if not scan.get("scanned"):
        ledger.add(
            Finding(
                finding_id="A-1",
                proposition="The executing runtime could not be read, so its capabilities are unknown.",
                surface="token_controls",
                status=Status.UNKNOWN,
                confidence=Confidence.UNKNOWN,
                chain_id=packet.observed_chain_id,
                address=packet.requested.address,
                coverage=str(scan.get("reason") or "eth_getCode did not return"),
                notes="A limit of the run, not a property of the token.",
            )
        )
        checks.append(Status.UNKNOWN)
        confidences.append(Confidence.UNKNOWN)
        finding_ids.append("A-1")
        summaries.append("Runtime unreadable.")
    else:
        capabilities = dict(scan.get("capabilities") or {})
        found = {name: items for name, items in capabilities.items() if name in CONTROL_FAMILIES}
        caveats = " ".join(describe_limits_from(scan))
        if found:
            for family, items in sorted(found.items()):
                severity = SEVERITY_BY_CAPABILITY.get(family, "medium")
                signatures = ", ".join(sorted(item["signature"] for item in items))
                finding_id = f"A-{len(finding_ids) + 1}"
                ledger.add(
                    Finding(
                        finding_id=finding_id,
                        proposition=(
                            f"The executing runtime exposes {family.replace('_', ' ')} "
                            f"entry points: {signatures}."
                        ),
                        surface="token_controls",
                        status=Status.ADVERSE,
                        confidence=Confidence.PROVEN,
                        chain_id=packet.observed_chain_id,
                        address=packet.requested.address,
                        evidence=[_runtime_evidence(packet, scan)],
                        decoding_basis="PUSH4 dispatcher scan over the deployed runtime",
                        alternatives=[
                            "A present selector shows an exposed entry point, not that "
                            "it is reachable or that anyone currently holds the "
                            "authority to call it — confirm the role holder separately."
                        ],
                        coverage=f"Selector level, executing runtime only. {caveats}",
                        stale_when="The contract is replaced, or the authority is moved or renounced.",
                        severity=severity,
                        material=family in ALWAYS_MATERIAL,
                    )
                )
                checks.append(Status.ADVERSE)
                confidences.append(Confidence.PROVEN)
                finding_ids.append(finding_id)
                severities.append(severity)
            summaries.append(
                f"{len(found)} privileged capability famil{'y' if len(found) == 1 else 'ies'} exposed."
            )
        elif scan.get("delegates") and not proxy.get("implementation"):
            # It delegates somewhere the standard slots did not name: the
            # dispatcher we scanned is not the whole system.
            ledger.add(
                Finding(
                    finding_id="A-1",
                    proposition=(
                        "The runtime DELEGATECALLs to a target that no standard proxy "
                        "slot names, so the code that actually executes was not read."
                    ),
                    surface="token_controls",
                    status=Status.UNKNOWN,
                    confidence=Confidence.UNKNOWN,
                    chain_id=packet.observed_chain_id,
                    address=packet.requested.address,
                    coverage="Standard slots read; the delegate target is computed or non-standard.",
                    notes="Resolve the target from storage or a trace before concluding anything.",
                )
            )
            checks.append(Status.UNKNOWN)
            confidences.append(Confidence.UNKNOWN)
            finding_ids.append("A-1")
            summaries.append("Delegates to unresolved code.")
        else:
            ledger.add(
                Finding(
                    finding_id="A-1",
                    proposition=(
                        "The executing runtime exposes no mint, balance-rewrite, "
                        "upgrade, arbitrary-call, transfer-restriction, extraction or "
                        "fee-control entry point."
                    ),
                    surface="token_controls",
                    status=Status.CLEAR,
                    confidence=Confidence.PROVEN,
                    chain_id=packet.observed_chain_id,
                    address=packet.requested.address,
                    evidence=[_runtime_evidence(packet, scan)],
                    decoding_basis="PUSH4 dispatcher scan over the deployed runtime",
                    coverage=(
                        "Selector level, executing runtime at the pinned block. "
                        "Absence is conclusive here only because this runtime does "
                        "not DELEGATECALL."
                    ),
                    stale_when="Never for this bytecode; a different deployment is a different target.",
                )
            )
            checks.append(Status.CLEAR)
            confidences.append(Confidence.PROVEN)
            finding_ids.append("A-1")
            summaries.append("No privileged capability found in this bytecode.")

    # --- can the code itself be replaced? -----------------------------
    if proxy.get("is_proxy"):
        authority = proxy.get("upgrade_authority")
        holder = f" held by {to_checksum(authority)}" if authority else ""
        finding_id = f"A-{len(finding_ids) + 1}"
        ledger.add(
            Finding(
                finding_id=finding_id,
                proposition=(
                    f"The target is a {proxy.get('pattern')} proxy: the code that runs "
                    f"can be replaced{holder}."
                ),
                surface="token_controls",
                status=Status.ADVERSE,
                confidence=Confidence.PROVEN,
                chain_id=packet.observed_chain_id,
                address=packet.requested.address,
                evidence=[_slot_evidence(packet, str(proxy.get("pattern")))],
                decoding_basis="EIP-1967 / 1822 / zeppelinos slots read at the pin",
                coverage="Standard proxy slots. A non-standard scheme would not appear here.",
                stale_when="Upgrade authority is moved to a timelock, or the proxy is frozen.",
                severity="critical",
                notes=(
                    "Whatever the current implementation permits, an administrator can "
                    "replace it with one that permits anything."
                ),
            )
        )
        checks.append(Status.ADVERSE)
        confidences.append(Confidence.PROVEN)
        finding_ids.append(finding_id)
        severities.append("critical")
        summaries.append("Upgradeable.")
        for entry in packet.scope:
            if entry.role == "upgrade authority" and entry.runtime_status == "no_code":
                summaries.append("Upgrade authority is an externally owned account.")

    return Rating(
        dimension="token_controls",
        check_statuses=checks or [Status.NOT_CHECKED],
        confidences=confidences or [Confidence.UNKNOWN],
        severity=_worst_severity(severities),
        likelihood="available to the authority holder at any time" if severities else "n/a",
        coverage=(
            "Deployed runtime of the executing address plus the standard proxy slots, "
            f"at {pin_basis}. Role holders and their thresholds are not resolved in this pass."
        ),
        time_basis=pin_basis,
        summary=" ".join(summaries) or "Not established.",
        finding_ids=finding_ids,
    )


def describe_limits_from(scan: Mapping[str, Any]) -> list[str]:
    """Caveats recorded by the scan itself, or the standing ones."""
    caveats = scan.get("caveats")
    if isinstance(caveats, list) and caveats:
        return [str(item) for item in caveats]
    from evm_dd.selectors import scan_runtime

    return describe_limits(scan_runtime("0x"))


def unexamined(reason: str, examined: set[str]) -> list[Rating]:
    """Every surface this run did not reach, stated rather than omitted."""
    return [
        Rating(
            dimension=name,
            check_statuses=[Status.NOT_CHECKED],
            confidences=[Confidence.UNKNOWN],
            coverage=reason,
            summary=description,
        )
        for name, description in DIMENSIONS
        if name not in examined
    ]


def assess(
    packet: TargetPacket,
    scan: Mapping[str, Any],
    ledger: Ledger,
    *,
    question: str = "",
    requirement: str = "the token's own controls are not unilaterally dangerous",
    mode: str = "focused",
    source_id: str = "",
) -> Report:
    """Rate what the evidence supports, and say so about the rest."""
    report = Report(packet=packet, ledger=ledger, mode=mode, source_id=source_id)

    report.rate(assess_token_controls(packet, scan, ledger))
    examined = {"token_controls"}

    for rating in unexamined(
        "Not reached: this pass reads the target's own code and proxy state. "
        "Liquidity, depth, concentration, launch, treasury, rewards, utility and "
        "dependencies need their own reads.",
        examined,
    ):
        report.rate(rating)

    # The verdict is issued against *every* surface, not just the ones this
    # pass reached. Narrowing the requirement does not license a favourable
    # call on a document where ten surfaces were never opened — that is the
    # unknown-as-a-pass failure, one level up. A critical adverse finding is
    # still decisive, because `derive_verdict` weighs blockers before gaps.
    report.verdict = derive_verdict(
        question=question or packet.decision_question or "What can privileged actors change or take?",
        requirement=requirement,
        ratings=report.ratings,
        pin_description=f"at block {packet.pin.number}",
        required_dimensions=DIMENSION_IDS,
    )
    # Lead with what the pass did establish, so an INSUFFICIENT EVIDENCE call
    # is not mistaken for having found nothing.
    for name in sorted(examined):
        rating = next(item for item in report.ratings if item.dimension == name)
        led = f"{name}: {rating.status.value} — {rating.summary}"
        # derive_verdict may already carry the bare summary; replace it rather
        # than stack a near-duplicate line above it.
        report.verdict.main_reasons = [
            item for item in report.verdict.main_reasons if item != rating.summary
        ]
        report.verdict.main_reasons.insert(0, led)
    report.verdict.would_change_this.extend(
        [
            "Any state change after the pinned block — re-run and compare the runtime hash.",
            "Resolving the role holders behind each exposed capability.",
        ]
    )
    report.verdict.unresolved_questions.extend(
        rating.dimension for rating in report.ordered_ratings()
        if rating.status is Status.NOT_CHECKED
    )
    return report
