import pytest

from evm_dd.evidence import (
    Confidence,
    CoverageLimitation,
    Evidence,
    EvidenceError,
    EvidenceKind,
    Finding,
    Ledger,
    Status,
    redact,
    weakest_confidence,
    worst_status,
)


def _onchain(summary: str = "eth_call owner()") -> Evidence:
    return Evidence(kind=EvidenceKind.RPC_CALL, summary=summary, query="eth_call")


@pytest.mark.parametrize(
    "statuses,expected",
    [
        ([Status.CLEAR, Status.CLEAR], Status.CLEAR),
        ([Status.CLEAR, Status.UNKNOWN], Status.UNKNOWN),
        ([Status.CLEAR, Status.NOT_CHECKED], Status.NOT_CHECKED),
        ([Status.UNKNOWN, Status.ADVERSE], Status.ADVERSE),
        ([Status.CLEAR, Status.NOT_APPLICABLE], Status.CLEAR),
        ([], Status.NOT_CHECKED),
    ],
)
def test_an_unknown_can_never_aggregate_to_a_pass(statuses, expected):
    assert worst_status(statuses) is expected


def test_confidence_aggregates_to_the_weakest_input():
    assert weakest_confidence([Confidence.PROVEN, Confidence.INFERRED]) is Confidence.INFERRED
    assert weakest_confidence([]) is Confidence.UNKNOWN


def test_proven_requires_primary_onchain_evidence():
    finding = Finding(
        finding_id="F-1",
        proposition="owner is the zero address",
        surface="token_controls",
        status=Status.CLEAR,
        confidence=Confidence.PROVEN,
        chain_id=1,
        address="0x" + "11" * 20,
        evidence=[Evidence(kind=EvidenceKind.EXPLORER, summary="explorer says renounced")],
    )
    assert any("primary onchain" in problem for problem in finding.defects())
    finding.evidence.append(_onchain())
    assert finding.defects() == []


def test_a_material_finding_may_not_rest_on_claims_alone():
    finding = Finding(
        finding_id="F-2",
        proposition="the treasury is multisig-controlled",
        surface="admin_treasury_custody",
        status=Status.CLEAR,
        confidence=Confidence.STRONG,
        chain_id=1,
        address="0x" + "11" * 20,
        evidence=[
            Evidence(kind=EvidenceKind.PROJECT_WEBSITE, summary="docs say 3-of-5"),
            Evidence(kind=EvidenceKind.SCANNER, summary="scanner agrees"),
        ],
    )
    assert any("rests only on claims" in problem for problem in finding.defects())


def test_a_counterfactual_result_can_never_be_proven_of_the_chain():
    finding = Finding(
        finding_id="F-3",
        proposition="a holder-sized sell succeeds",
        surface="sellability_and_depth",
        status=Status.CLEAR,
        confidence=Confidence.PROVEN,
        chain_id=1,
        address="0x" + "11" * 20,
        counterfactual=True,
        evidence=[Evidence(kind=EvidenceKind.FORK_SIMULATION, summary="fork sell succeeded")],
    )
    problems = finding.defects()
    assert any("counterfactual" in problem for problem in problems)


def test_unknown_status_may_not_carry_a_confident_claim():
    finding = Finding(
        finding_id="F-4",
        proposition="concentration is low",
        surface="current_concentration",
        status=Status.UNKNOWN,
        confidence=Confidence.STRONG,
        chain_id=1,
        address="0x" + "11" * 20,
    )
    assert any("confidence=unknown" in problem for problem in finding.defects())


def test_an_adverse_finding_must_say_what_would_make_it_stale():
    finding = Finding(
        finding_id="F-5",
        proposition="the owner can mint",
        surface="token_controls",
        status=Status.ADVERSE,
        confidence=Confidence.PROVEN,
        chain_id=1,
        address="0x" + "11" * 20,
        evidence=[_onchain()],
    )
    assert any("stale" in problem for problem in finding.defects())
    finding.stale_when = "ownership is renounced or the mint path is removed"
    assert finding.defects() == []


def test_a_coverage_limitation_is_a_separate_kind_of_thing_from_a_finding():
    ledger = Ledger()
    ledger.limit(
        CoverageLimitation(
            scope="holder replay",
            reason="rate limited after 400 requests",
            attempted="eth_getLogs chunked",
            consequence="concentration stays unknown",
        )
    )
    payload = ledger.to_dict()
    assert payload["findings"] == []
    assert payload["coverage_limitations"][0]["retryable"] is True


def test_duplicate_finding_ids_are_refused():
    ledger = Ledger()
    finding = Finding(
        finding_id="F-6",
        proposition="x",
        surface="token_controls",
        status=Status.UNKNOWN,
        confidence=Confidence.UNKNOWN,
        chain_id=1,
        address="0x" + "11" * 20,
    )
    ledger.add(finding)
    with pytest.raises(EvidenceError):
        ledger.add(finding)


@pytest.mark.parametrize(
    "text,secret",
    [
        ("https://eth.example.org/v2/abcdef0123456789abcdef0123456789", "abcdef0123456789"),
        ('{"api_key": "sk-live-abcdef"}', "sk-live-abcdef"),
        ("Authorization: Bearer sk-live-abcdef", "sk-live-abcdef"),
        ("https://user:hunter2@rpc.example.org/", "hunter2"),
    ],
)
def test_credentials_are_redacted_before_anything_is_persisted(text, secret):
    assert secret not in redact(text)


def test_redaction_leaves_addresses_and_block_numbers_alone():
    text = "eth_call to 0x1111111111111111111111111111111111111111 at block 21633856"
    assert redact(text) == text
