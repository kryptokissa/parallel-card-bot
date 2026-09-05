"""Worked examples — entirely synthetic, and labelled as such everywhere.

These exist to show what a correct answer *looks like* on the situations
that most often get reported wrongly, and to give the validator something
to be tested against. Every address is a repeated-byte pattern, every
block hash is patterned, and nothing here is an observation about any real
token. Nothing in this module may ever be presented as a live finding.

The six cases are the ones where a plausible-sounding report is usually
wrong:

1. Locked canonical liquidity next to a removable side pool.
2. Fixed supply that still cannot be exited at holder size.
3. An immutable token wrapped in an upgradeable reward layer.
4. Launch wallets that sold and rebought, which is not a cash-out.
5. A vault full of synthetic claims with no proven underlying exit.
6. An RPC failure, which is a limit of the run and never a finding.
"""

from __future__ import annotations

from typing import Any, Callable

from evm_dd.addresses import TargetRef
from evm_dd.evidence import (
    Confidence,
    CoverageLimitation,
    Evidence,
    EvidenceKind,
    Finding,
    Ledger,
    Status,
)
from evm_dd.pins import BlockPin
from evm_dd.report import DIMENSION_IDS, Rating, Report, derive_verdict
from evm_dd.target import ScopeAddress, TargetPacket, metadata_from_calls

SYNTHETIC_NOTICE = (
    "SYNTHETIC EXAMPLE — every address, balance and hash below is invented "
    "to illustrate the shape of a correct answer. It is not an observation "
    "about any deployed token and must never be quoted as one."
)

TOKEN = "0x1111111111111111111111111111111111111111"
POOL = "0x4444444444444444444444444444444444444444"
SIDE_POOL = "0x8888888888888888888888888888888888888888"
LOCKER = "0x5555555555555555555555555555555555555555"
DEPLOYER = "0x6666666666666666666666666666666666666666"
REWARDER = "0x9999999999999999999999999999999999999999"
VAULT = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
QUOTE = "0x7777777777777777777777777777777777777777"

BLOCK_NUMBER = 21_633_856
BLOCK_HASH = "0x" + "ab" * 32
BLOCK_TIMESTAMP = 1_724_950_000


def _pin(chain_id: int = 1) -> BlockPin:
    return BlockPin(
        chain_id=chain_id,
        number=BLOCK_NUMBER,
        block_hash=BLOCK_HASH,
        timestamp=BLOCK_TIMESTAMP,
        parent_hash="0x" + "cd" * 32,
        source="synthetic example",
    )


def _header() -> dict[str, Any]:
    return {
        "number": hex(BLOCK_NUMBER),
        "hash": BLOCK_HASH,
        "timestamp": hex(BLOCK_TIMESTAMP),
        "parentHash": "0x" + "cd" * 32,
    }


def _evidence(summary: str, *, kind: EvidenceKind = EvidenceKind.RPC_CALL, address: str = TOKEN) -> Evidence:
    return Evidence(
        kind=kind,
        summary=summary,
        query=f"eth_call at block {BLOCK_NUMBER} (synthetic)",
        result_digest="0x" + "00" * 4,
        chain_id=1,
        address=address,
        block_number=BLOCK_NUMBER,
        source="synthetic example",
    )


def _packet(question: str, *, scope: list[ScopeAddress] | None = None) -> TargetPacket:
    return TargetPacket(
        requested=TargetRef(1, TOKEN),
        observed_chain_id=1,
        pin=_pin(),
        header=_header(),
        metadata=metadata_from_calls(
            name="Example Token", symbol="EXAMPLE", decimals=18, total_supply=10**24
        ),
        runtime_hash="0x" + "ef" * 32,
        runtime_size=4_820,
        scope=scope
        or [
            ScopeAddress(
                address=TOKEN,
                chain_id=1,
                role="target token",
                provenance="requested by the user",
                runtime_status="code",
            )
        ],
        decision_question=question,
        materiality="Authority to mint, upgrade, seize, restrict transfers or "
        "remove LP principal is material at any size.",
        endpoint_identity="synthetic://example",
        known_limitations=[SYNTHETIC_NOTICE],
    )


def _base_ratings(**overrides: Rating) -> list[Rating]:
    """All eleven surfaces, unexamined unless the example rates them."""
    ratings = [
        Rating(
            dimension=name,
            check_statuses=[Status.NOT_CHECKED],
            confidences=[Confidence.UNKNOWN],
            coverage="not reached in this synthetic example",
            summary="not examined in this example",
        )
        for name in DIMENSION_IDS
    ]
    by_name = {rating.dimension: rating for rating in ratings}
    by_name.update(overrides)
    return [by_name[name] for name in DIMENSION_IDS]


def _finish(report: Report, requirement: str, required: list[str]) -> Report:
    report.verdict = derive_verdict(
        question=report.packet.decision_question,
        requirement=requirement,
        ratings=report.ratings,
        pin_description=f"at block {BLOCK_NUMBER}",
        required_dimensions=required,
    )
    report.verdict.would_change_this.append(
        "Any state change after the pinned block; re-run and compare."
    )
    return report


# ---------------------------------------------------------------------------
# 1. locked canonical liquidity, removable side pool


def locked_canonical_liquidity_with_removable_side_pool() -> Report:
    ledger = Ledger()
    ledger.add(
        Finding(
            finding_id="LP-1",
            proposition=(
                "The canonical pool's full-range position is held by a locker "
                "whose unlock timestamp is after the pinned block, and the "
                "locker exposes no rescue or arbitrary-call path."
            ),
            surface="canonical_lp_custody",
            status=Status.CLEAR,
            confidence=Confidence.PROVEN,
            chain_id=1,
            address=LOCKER,
            evidence=[
                _evidence("positions(7) -> liquidity 4.1e21, full range", address=POOL),
                _evidence("ownerOf(7) -> locker", address=LOCKER),
                _evidence(
                    "locker runtime scan: no rescue, sweep or execute selector",
                    kind=EvidenceKind.RUNTIME_BYTECODE,
                    address=LOCKER,
                ),
            ],
            decoding_basis="NonfungiblePositionManager.positions ABI; ERC-721 ownerOf",
            coverage="One position id, resolved from the pool's mint logs.",
            stale_when="The lock expires, or the locker is upgraded.",
            severity="informational",
        )
    )
    ledger.add(
        Finding(
            finding_id="LP-2",
            proposition=(
                "A second pool on the same pair at a different fee tier holds "
                "18% of observed depth in a position owned directly by the "
                "launch signer, withdrawable at any time."
            ),
            surface="side_pool_removal_risk",
            status=Status.ADVERSE,
            confidence=Confidence.PROVEN,
            chain_id=1,
            address=SIDE_POOL,
            evidence=[
                _evidence("factory.getPool(token, quote, 10000) -> side pool", address=SIDE_POOL),
                _evidence("positions(41) -> liquidity 9.0e20", address=SIDE_POOL),
                _evidence("ownerOf(41) -> launch signer", address=DEPLOYER),
            ],
            decoding_basis="factory getPool; position manager positions/ownerOf",
            alternatives=[
                "The signer could be a custodian acting for someone else; "
                "custody is observed, control is inferred."
            ],
            coverage=(
                "Searched three fee tiers on one factory against two quote "
                "assets, blocks 19,000,000–21,633,856. Other venues not searched."
            ),
            stale_when="The position is transferred, locked, or withdrawn.",
            severity="high",
        )
    )
    report = Report(
        packet=_packet("Is the liquidity locked?"),
        ledger=ledger,
        mode="focused",
        source_id="example-locked-canonical",
    )
    for rating in _base_ratings(
        canonical_lp_custody=Rating(
            dimension="canonical_lp_custody",
            check_statuses=[Status.CLEAR],
            confidences=[Confidence.PROVEN],
            severity="informational",
            likelihood="n/a",
            coverage="One canonical position, locked past the pin.",
            time_basis=f"block {BLOCK_NUMBER}",
            summary="Canonical principal is not removable at the pinned block.",
            finding_ids=["LP-1"],
        ),
        side_pool_removal_risk=Rating(
            dimension="side_pool_removal_risk",
            check_statuses=[Status.ADVERSE],
            confidences=[Confidence.PROVEN],
            severity="high",
            likelihood="available to a single key at any time",
            coverage="Three fee tiers, one factory, two quote assets.",
            time_basis=f"block {BLOCK_NUMBER}",
            summary="18% of observed depth sits in an unlocked side position.",
            finding_ids=["LP-2"],
        ),
    ):
        report.rate(rating)
    return _finish(
        report,
        "liquidity principal cannot be withdrawn by any single party",
        ["canonical_lp_custody", "side_pool_removal_risk"],
    )


# ---------------------------------------------------------------------------
# 2. fixed supply, severe exit degradation


def fixed_supply_with_severe_exit_degradation() -> Report:
    ledger = Ledger()
    ledger.add(
        Finding(
            finding_id="TC-1",
            proposition=(
                "The executing runtime exposes no mint, burn-from, rebase, "
                "pause, blacklist or upgrade selector, and does not DELEGATECALL."
            ),
            surface="token_controls",
            status=Status.CLEAR,
            confidence=Confidence.PROVEN,
            chain_id=1,
            address=TOKEN,
            evidence=[
                _evidence(
                    "runtime scan of the target: 24 selectors, none in the "
                    "supply, restriction or upgrade families",
                    kind=EvidenceKind.RUNTIME_BYTECODE,
                )
            ],
            decoding_basis="PUSH4 dispatcher scan over deployed runtime",
            coverage=(
                "Selector-level scan of the deployed runtime. A present "
                "selector shows an entry point, not reachability; absence is "
                "conclusive here only because the runtime has no DELEGATECALL."
            ),
            stale_when="Never for this bytecode; a new deployment is a new target.",
        )
    )
    ledger.add(
        Finding(
            finding_id="SD-1",
            proposition=(
                "A read-only quote for a median holder's balance returns 91% "
                "less per unit than a 0.01% probe size on the same route at "
                "the pinned block."
            ),
            surface="sellability_and_depth",
            status=Status.ADVERSE,
            confidence=Confidence.STRONG,
            chain_id=1,
            address=POOL,
            evidence=[
                _evidence("quoteExactInputSingle probe size -> 1.00 per unit", address=POOL),
                _evidence("quoteExactInputSingle holder size -> 0.09 per unit", address=POOL),
                _evidence(
                    "successful historical sell of a comparable size 40 days "
                    "before the pin",
                    kind=EvidenceKind.TRANSACTION_RECEIPT,
                    address=POOL,
                ),
            ],
            decoding_basis="Quoter return values; receipt with the quote-asset balance delta",
            alternatives=[
                "Depth may be restored by new liquidity at any time; this is a "
                "statement about the pinned state, not a permanent property."
            ],
            coverage=(
                "One route, two sizes, quoted at the pin. Quotes are not "
                "realised exits."
            ),
            stale_when="Liquidity is added, or the position range moves.",
            severity="high",
        )
    )
    report = Report(
        packet=_packet("Can I exit a median holder's position?"),
        ledger=ledger,
        mode="focused",
        source_id="example-exit-depth",
    )
    for rating in _base_ratings(
        token_controls=Rating(
            dimension="token_controls",
            check_statuses=[Status.CLEAR],
            confidences=[Confidence.PROVEN],
            coverage="Deployed runtime, selector level, no delegatecall.",
            time_basis=f"block {BLOCK_NUMBER}",
            summary="Supply and transfer behaviour are fixed in this bytecode.",
            finding_ids=["TC-1"],
        ),
        sellability_and_depth=Rating(
            dimension="sellability_and_depth",
            check_statuses=[Status.ADVERSE],
            confidences=[Confidence.STRONG],
            severity="high",
            likelihood="certain at the tested size and state",
            coverage="One route, two sizes, at the pin.",
            time_basis=f"block {BLOCK_NUMBER}",
            summary="Sellable at probe size; 91% per-unit degradation at holder size.",
            finding_ids=["SD-1"],
        ),
    ):
        report.rate(rating)
    return _finish(
        report,
        "a median holder can exit without catastrophic degradation",
        ["token_controls", "sellability_and_depth"],
    )


# ---------------------------------------------------------------------------
# 3. immutable token, upgradeable reward layer


def upgradeable_reward_layer_around_immutable_token() -> Report:
    ledger = Ledger()
    ledger.add(
        Finding(
            finding_id="TC-2",
            proposition="The token contract is not a proxy and exposes no upgrade path.",
            surface="token_controls",
            status=Status.CLEAR,
            confidence=Confidence.PROVEN,
            chain_id=1,
            address=TOKEN,
            evidence=[
                _evidence("all standard proxy slots read empty at the pin", kind=EvidenceKind.STORAGE_SLOT),
                _evidence("runtime scan: no DELEGATECALL", kind=EvidenceKind.RUNTIME_BYTECODE),
            ],
            decoding_basis="EIP-1967/1822/zeppelinos slots; opcode scan",
            coverage="Standard slots plus opcode scan of the deployed runtime.",
            stale_when="Not applicable to this bytecode.",
        )
    )
    ledger.add(
        Finding(
            finding_id="RW-1",
            proposition=(
                "Every advertised holder reward is paid by a separate "
                "rewarder contract behind an EIP-1967 proxy whose admin is a "
                "single externally owned account. That account can replace the "
                "reward logic in one transaction."
            ),
            surface="admin_treasury_custody",
            status=Status.ADVERSE,
            confidence=Confidence.PROVEN,
            chain_id=1,
            address=REWARDER,
            evidence=[
                _evidence("eip1967 implementation slot -> logic contract", kind=EvidenceKind.STORAGE_SLOT, address=REWARDER),
                _evidence("eip1967 admin slot -> externally owned account", kind=EvidenceKind.STORAGE_SLOT, address=REWARDER),
                _evidence("eth_getCode(admin) -> empty, so it is an EOA, not a timelock", address=DEPLOYER),
            ],
            decoding_basis="EIP-1967 slots read at the pin; code check on the admin",
            alternatives=[
                "The admin key could be held in a multisig wallet off-chain; "
                "observed onchain it is a single account with no code."
            ],
            coverage="The rewarder and its admin. Other periphery not enumerated.",
            stale_when="Admin is moved to a timelock or multisig, or upgraded away.",
            severity="critical",
        )
    )
    report = Report(
        packet=_packet(
            "The token is immutable — does that make the rewards safe?",
            scope=[
                ScopeAddress(
                    address=TOKEN,
                    chain_id=1,
                    role="target token",
                    provenance="requested by the user",
                    runtime_status="code",
                ),
                ScopeAddress(
                    address=REWARDER,
                    chain_id=1,
                    role="reward distributor (proxy)",
                    provenance="named in the project's own documentation, then confirmed onchain",
                    runtime_status="code",
                ),
            ],
        ),
        ledger=ledger,
        mode="focused",
        source_id="example-upgradeable-rewards",
    )
    for rating in _base_ratings(
        token_controls=Rating(
            dimension="token_controls",
            check_statuses=[Status.CLEAR],
            confidences=[Confidence.PROVEN],
            coverage="Token contract only.",
            time_basis=f"block {BLOCK_NUMBER}",
            summary="The token itself is immutable.",
            finding_ids=["TC-2"],
        ),
        admin_treasury_custody=Rating(
            dimension="admin_treasury_custody",
            check_statuses=[Status.ADVERSE],
            confidences=[Confidence.PROVEN],
            severity="critical",
            likelihood="available to one key at any time",
            coverage="Rewarder proxy and its admin.",
            time_basis=f"block {BLOCK_NUMBER}",
            summary="Reward logic is replaceable by a single EOA.",
            finding_ids=["RW-1"],
        ),
    ):
        report.rate(rating)
    return _finish(
        report,
        "the economic system around the token is not unilaterally changeable",
        ["token_controls", "admin_treasury_custody"],
    )


# ---------------------------------------------------------------------------
# 4. launch wallets sold and rebought


def launch_wallets_sold_and_rebought() -> Report:
    ledger = Ledger()
    ledger.add(
        Finding(
            finding_id="LI-1",
            proposition=(
                "Eleven addresses funded by the launch signer within one hour "
                "of deployment sold their entire allocation over four days, "
                "and 62% of the proceeds were used to buy the same token back "
                "into different addresses."
            ),
            surface="launch_integrity",
            status=Status.ADVERSE,
            confidence=Confidence.STRONG,
            chain_id=1,
            address=DEPLOYER,
            evidence=[
                _evidence("11 funding transfers from the launch signer", kind=EvidenceKind.DECODED_LOG, address=DEPLOYER),
                _evidence("sell receipts with pool-side quote-asset deltas", kind=EvidenceKind.TRANSACTION_RECEIPT, address=POOL),
                _evidence("rebuy receipts crediting nine new addresses", kind=EvidenceKind.TRANSACTION_RECEIPT, address=POOL),
            ],
            decoding_basis=(
                "Transfer logs for the cohort; swap receipts proved from pool "
                "mechanics and realised balance deltas, not router transfers"
            ),
            alternatives=[
                "Shared funding and timing do not establish common ownership, "
                "coordination or a single beneficiary.",
                "The rebuy addresses may belong to unrelated buyers whose "
                "purchases the cohort's sales merely funded.",
            ],
            coverage=(
                "Cohort defined as: addresses receiving native currency from "
                "the launch signer between deployment and deployment+1h, "
                "blocks 21,000,000–21,633,856. Addresses funded by other "
                "routes are outside it."
            ),
            stale_when="Further transfers change the inventory picture.",
            severity="medium",
            notes=(
                "Described as market-mediated redistribution. Proceeds are not "
                "called profit: no cost basis was established, and retained "
                "inventory is still held."
            ),
        )
    )
    report = Report(
        packet=_packet("Did the launch wallets cash out?"),
        ledger=ledger,
        mode="focused",
        source_id="example-launch-cohort",
    )
    for rating in _base_ratings(
        launch_integrity=Rating(
            dimension="launch_integrity",
            check_statuses=[Status.ADVERSE],
            confidences=[Confidence.STRONG],
            severity="medium",
            likelihood="observed, historical",
            coverage="One cohort rule, one funding route, a 633k block window.",
            time_basis=f"blocks 21,000,000–{BLOCK_NUMBER}",
            summary=(
                "The cohort sold in full and rebought most of it into new "
                "addresses — redistribution, not a proven cash-out."
            ),
            finding_ids=["LI-1"],
        ),
    ):
        report.rate(rating)
    return _finish(report, "launch allocation was not quietly extracted", ["launch_integrity"])


# ---------------------------------------------------------------------------
# 5. vault of synthetic claims


def vault_of_synthetic_claims_without_proven_exit() -> Report:
    ledger = Ledger()
    ledger.add(
        Finding(
            finding_id="BK-1",
            proposition=(
                "The vault's balance is denominated in a claim token issued by "
                "the same operator, not in the underlying asset the "
                "documentation names as backing."
            ),
            surface="utility_and_redemption",
            status=Status.ADVERSE,
            confidence=Confidence.PROVEN,
            chain_id=1,
            address=VAULT,
            evidence=[
                _evidence("vault balanceOf(claimToken) -> 1.2e24", address=VAULT),
                _evidence("vault balanceOf(underlying) -> 0", address=VAULT),
                _evidence("claimToken.issuer() -> the same operator", address=REWARDER),
            ],
            decoding_basis="ERC-20 balanceOf on both assets at the pin",
            coverage="Two assets at one address at the pin.",
            stale_when="The vault acquires the underlying, or the claim is redeemed.",
            severity="high",
        )
    )
    ledger.add(
        Finding(
            finding_id="BK-2",
            proposition=(
                "No redemption of the claim token for the underlying has ever "
                "settled: the redeem path exists but every historical call "
                "reverted, and none produced an underlying-asset balance delta."
            ),
            surface="utility_and_redemption",
            status=Status.UNKNOWN,
            confidence=Confidence.UNKNOWN,
            chain_id=1,
            address=VAULT,
            evidence=[
                _evidence("redeem() selector present in runtime", kind=EvidenceKind.RUNTIME_BYTECODE, address=VAULT)
            ],
            decoding_basis="Runtime selector scan; receipt search over the searched range",
            coverage=(
                "Receipt search covered blocks 21,000,000–21,633,856 only. "
                "Earlier redemptions, if any, were not searched."
            ),
            stale_when="A settled redemption appears.",
            notes=(
                "A returned success flag or an emitted event would not settle "
                "this. A decisive answer needs a receipt plus the intended "
                "underlying-asset balance delta."
            ),
        )
    )
    report = Report(
        packet=_packet("Is the token backed?"),
        ledger=ledger,
        mode="focused",
        source_id="example-synthetic-backing",
    )
    for rating in _base_ratings(
        utility_and_redemption=Rating(
            dimension="utility_and_redemption",
            check_statuses=[Status.ADVERSE, Status.UNKNOWN],
            confidences=[Confidence.PROVEN, Confidence.UNKNOWN],
            severity="high",
            likelihood="structural",
            coverage="Vault holdings at the pin; redemption history only over the searched range.",
            time_basis=f"block {BLOCK_NUMBER}",
            summary=(
                "The vault holds a synthetic claim, not the underlying, and no "
                "redemption has been shown to settle."
            ),
            finding_ids=["BK-1", "BK-2"],
        ),
    ):
        report.rate(rating)
    return _finish(report, "holder claims are enforceable against a real asset", ["utility_and_redemption"])


# ---------------------------------------------------------------------------
# 6. an RPC failure is never a finding


def rpc_failure_stays_a_coverage_limitation() -> Report:
    ledger = Ledger()
    ledger.limit(
        CoverageLimitation(
            scope="holder enumeration (Transfer replay)",
            reason="archive node returned 'missing trie node' before block 20,400,000",
            attempted="eth_getLogs over Transfer topic0, chunked by 50,000 blocks",
            consequence=(
                "Concentration before that block is unknown. It is not low, "
                "and it is not high — it is unmeasured."
            ),
            retryable=True,
        )
    )
    ledger.add(
        Finding(
            finding_id="CN-1",
            proposition=(
                "Concentration could not be established for the full history "
                "of the token."
            ),
            surface="current_concentration",
            status=Status.UNKNOWN,
            confidence=Confidence.UNKNOWN,
            chain_id=1,
            address=TOKEN,
            evidence=[],
            material=True,
            coverage="Replay covered blocks 20,400,000–21,633,856 only.",
            notes=(
                "The node's pruning is a property of the endpoint, not of the "
                "token. Nothing may be rated clear on the strength of it."
            ),
        )
    )
    report = Report(
        packet=_packet("How concentrated is ownership?"),
        ledger=ledger,
        mode="focused",
        source_id="example-coverage-limit",
    )
    for rating in _base_ratings(
        current_concentration=Rating(
            dimension="current_concentration",
            check_statuses=[Status.UNKNOWN],
            confidences=[Confidence.UNKNOWN],
            coverage="Partial replay: blocks 20,400,000 onward only.",
            time_basis=f"block {BLOCK_NUMBER}",
            summary="Unknown because historical state was unavailable.",
            finding_ids=["CN-1"],
        ),
    ):
        report.rate(rating)
    return _finish(report, "ownership is not dangerously concentrated", ["current_concentration"])


EXAMPLES: dict[str, Callable[[], Report]] = {
    "locked-canonical-removable-side-pool": locked_canonical_liquidity_with_removable_side_pool,
    "fixed-supply-severe-exit-degradation": fixed_supply_with_severe_exit_degradation,
    "upgradeable-reward-layer": upgradeable_reward_layer_around_immutable_token,
    "launch-wallets-sold-and-rebought": launch_wallets_sold_and_rebought,
    "vault-of-synthetic-claims": vault_of_synthetic_claims_without_proven_exit,
    "rpc-failure-is-a-coverage-limit": rpc_failure_stays_a_coverage_limitation,
}


def build(name: str) -> dict[str, Any]:
    """Render one example as a report document, carrying its synthetic notice."""
    if name not in EXAMPLES:
        raise KeyError(f"unknown example {name!r}; expected one of {sorted(EXAMPLES)}")
    payload = EXAMPLES[name]().to_dict()
    payload["synthetic"] = True
    payload["synthetic_notice"] = SYNTHETIC_NOTICE
    payload["example_id"] = name
    return payload
