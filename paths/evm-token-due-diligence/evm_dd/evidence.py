"""Evidence typing, the finding-to-evidence ledger, and the status algebra.

Three rules are enforced here in code rather than left to prose:

1. An unknown is never a pass. ``Status`` has no value that lets a check
   that did not complete count as a clean one, and the aggregation helpers
   refuse to produce a favourable status from an incomplete input.
2. A material onchain claim must cite primary onchain evidence. Explorer
   pages, dashboards, scanner scores and project copy corroborate; they do
   not establish.
3. An RPC failure is a coverage limitation, not a finding about the token.
   ``CoverageLimitation`` is a separate type and ``Finding`` refuses to be
   built out of one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Sequence


class Status(str, Enum):
    """Outcome of a single check.

    There is deliberately no ``PASS`` for "we did not look".
    """

    CLEAR = "clear"                    # checked; came back clean at the pin
    ADVERSE = "adverse"                # checked; came back adverse
    UNKNOWN = "unknown"                # attempted; could not be established
    NOT_CHECKED = "not_checked"        # never attempted
    NOT_APPLICABLE = "not_applicable"  # cannot apply to this token's design

    @property
    def is_favourable(self) -> bool:
        return self is Status.CLEAR

    @property
    def is_settled(self) -> bool:
        """True when the check produced an answer either way."""
        return self in (Status.CLEAR, Status.ADVERSE, Status.NOT_APPLICABLE)


class Confidence(str, Enum):
    PROVEN = "proven"        # primary onchain evidence, decoded, reproducible
    STRONG = "strong"        # corroborated, one defensible reading
    INFERRED = "inferred"    # consistent with evidence; alternatives survive
    UNKNOWN = "unknown"      # not established

    @property
    def rank(self) -> int:
        return {"unknown": 0, "inferred": 1, "strong": 2, "proven": 3}[self.value]


class EvidenceKind(str, Enum):
    # Primary onchain: the deployed system answering for itself.
    RUNTIME_BYTECODE = "runtime_bytecode"
    STORAGE_SLOT = "storage_slot"
    RPC_CALL = "rpc_call"
    TRANSACTION_RECEIPT = "transaction_receipt"
    DECODED_LOG = "decoded_log"
    CALLDATA = "calldata"
    BLOCK_HEADER = "block_header"
    # Counterfactual: true of a fork, not of the chain.
    FORK_SIMULATION = "fork_simulation"
    # Corroboration: useful for discovery, never dispositive.
    EXPLORER = "explorer"
    DASHBOARD = "dashboard"
    SCANNER = "scanner"
    THIRD_PARTY_API = "third_party_api"
    # Claims: what someone says about the system.
    PROJECT_WEBSITE = "project_website"
    PROJECT_DOCUMENT = "project_document"
    SOURCE_REPOSITORY = "source_repository"
    SOCIAL_POST = "social_post"


PRIMARY_ONCHAIN_KINDS = frozenset(
    {
        EvidenceKind.RUNTIME_BYTECODE,
        EvidenceKind.STORAGE_SLOT,
        EvidenceKind.RPC_CALL,
        EvidenceKind.TRANSACTION_RECEIPT,
        EvidenceKind.DECODED_LOG,
        EvidenceKind.CALLDATA,
        EvidenceKind.BLOCK_HEADER,
    }
)

COUNTERFACTUAL_KINDS = frozenset({EvidenceKind.FORK_SIMULATION})

CLAIM_KINDS = frozenset(
    {
        EvidenceKind.PROJECT_WEBSITE,
        EvidenceKind.PROJECT_DOCUMENT,
        EvidenceKind.SOURCE_REPOSITORY,
        EvidenceKind.SOCIAL_POST,
    }
)

_CREDENTIAL_RE = re.compile(
    r"(?i)\b(api[-_]?key|apikey|access[-_]?token|auth[-_]?token|token|"
    r"secret|password|passwd|private[-_]?key|mnemonic|seed[-_]?phrase)\b"
    r"[\"']?\s*[=:]\s*[\"']?[^\s&\"',}]+"
)
_BEARER_RE = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._\-+/=]+")
_URL_CREDENTIAL_RE = re.compile(r"(?i)(https?://)([^/@\s]+)@")
_URL_KEY_SEGMENT_RE = re.compile(r"(?i)(/v\d/|/)([0-9a-f]{24,}|[A-Za-z0-9_-]{32,})(?=/|$)")


def redact(text: str) -> str:
    """Strip credential-shaped material from anything that will be persisted.

    Endpoint URLs routinely carry the key in a path segment. Query
    parameters are redacted by name; long opaque path segments are redacted
    by shape.
    """
    if not isinstance(text, str):
        return text
    redacted = _CREDENTIAL_RE.sub(lambda m: f"{m.group(1)}=<redacted>", text)
    redacted = _BEARER_RE.sub(lambda m: f"{m.group(1)} <redacted>", redacted)
    redacted = _URL_CREDENTIAL_RE.sub(r"\1<redacted>@", redacted)
    redacted = _URL_KEY_SEGMENT_RE.sub(r"\1<redacted>", redacted)
    return redacted


class EvidenceError(ValueError):
    pass


@dataclass(frozen=True)
class Evidence:
    """One reproducible artifact.

    ``query`` must be enough for another person to run the same read and get
    the same bytes: method, params, endpoint identity (not its credential),
    and the pin it was taken at.
    """

    kind: EvidenceKind
    summary: str
    query: str = ""
    result_digest: str = ""
    chain_id: int | None = None
    address: str | None = None
    block_number: int | None = None
    tx_hash: str | None = None
    source: str = ""
    retrieved_at: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "query", redact(self.query))
        object.__setattr__(self, "source", redact(self.source))
        object.__setattr__(self, "summary", redact(self.summary))

    @property
    def tier(self) -> str:
        if self.kind in PRIMARY_ONCHAIN_KINDS:
            return "primary_onchain"
        if self.kind in COUNTERFACTUAL_KINDS:
            return "counterfactual"
        if self.kind in CLAIM_KINDS:
            return "claim"
        return "corroboration"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "tier": self.tier,
            "summary": self.summary,
            "query": self.query,
            "result_digest": self.result_digest,
            "chain_id": self.chain_id,
            "address": self.address,
            "block_number": self.block_number,
            "tx_hash": self.tx_hash,
            "source": self.source,
            "retrieved_at": self.retrieved_at,
        }


@dataclass(frozen=True)
class CoverageLimitation:
    """Something the run could not see. Never a statement about the token."""

    scope: str
    reason: str
    attempted: str = ""
    consequence: str = ""
    retryable: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "attempted", redact(self.attempted))
        object.__setattr__(self, "reason", redact(self.reason))

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "reason": self.reason,
            "attempted": self.attempted,
            "consequence": self.consequence,
            "retryable": self.retryable,
        }


CLAIM_TIERS = frozenset({"claim", "corroboration"})


@dataclass
class Finding:
    """One proposition, with everything needed to check or refute it."""

    finding_id: str
    proposition: str
    surface: str
    status: Status
    confidence: Confidence
    chain_id: int
    address: str
    evidence: list[Evidence] = field(default_factory=list)
    decoding_basis: str = ""
    alternatives: list[str] = field(default_factory=list)
    coverage: str = ""
    stale_when: str = ""
    material: bool = True
    counterfactual: bool = False
    severity: str = "informational"
    notes: str = ""

    _SEVERITIES = ("informational", "low", "medium", "high", "critical")

    def __post_init__(self) -> None:
        if isinstance(self.status, str):
            self.status = Status(self.status)
        if isinstance(self.confidence, str):
            self.confidence = Confidence(self.confidence)
        if self.severity not in self._SEVERITIES:
            raise EvidenceError(
                f"{self.finding_id}: severity must be one of {self._SEVERITIES}"
            )
        if isinstance(self.proposition, CoverageLimitation):  # pragma: no cover
            raise EvidenceError("a coverage limitation is not a finding")
        self.notes = redact(self.notes)

    @property
    def tiers(self) -> set[str]:
        return {item.tier for item in self.evidence}

    def defects(self) -> list[str]:
        """Ledger rules this finding breaks. Empty means the row is sound."""
        problems: list[str] = []
        if not self.proposition.strip():
            problems.append("proposition is empty")
        if self.status is Status.UNKNOWN and self.confidence is not Confidence.UNKNOWN:
            problems.append(
                "status=unknown must carry confidence=unknown "
                f"(got {self.confidence.value})"
            )
        if self.status is Status.NOT_CHECKED and self.confidence is not Confidence.UNKNOWN:
            problems.append("status=not_checked must carry confidence=unknown")
        if self.status.is_settled and self.confidence is Confidence.UNKNOWN:
            problems.append(
                f"status={self.status.value} claims an answer but confidence=unknown"
            )
        if self.confidence is Confidence.PROVEN:
            if "primary_onchain" not in self.tiers:
                problems.append(
                    "confidence=proven requires at least one primary onchain "
                    f"evidence row (has: {sorted(self.tiers) or 'none'})"
                )
            if self.counterfactual:
                problems.append(
                    "a counterfactual (fork) result cannot be proven of the chain"
                )
        if self.material and self.status.is_settled and not self.evidence:
            problems.append("material settled finding cites no evidence")
        if (
            self.material
            and self.status.is_settled
            and self.evidence
            and self.tiers <= CLAIM_TIERS
        ):
            problems.append(
                "material finding rests only on claims/corroboration; cite the "
                "deployed system or downgrade the status to unknown"
            )
        if self.status is Status.ADVERSE and not self.stale_when:
            problems.append("adverse finding must say what would make it stale")
        return problems

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "proposition": self.proposition,
            "surface": self.surface,
            "status": self.status.value,
            "confidence": self.confidence.value,
            "severity": self.severity,
            "material": self.material,
            "counterfactual": self.counterfactual,
            "chain_id": self.chain_id,
            "address": self.address,
            "decoding_basis": self.decoding_basis,
            "alternatives": list(self.alternatives),
            "coverage": self.coverage,
            "stale_when": self.stale_when,
            "notes": self.notes,
            "evidence": [item.to_dict() for item in self.evidence],
        }



def worst_status(statuses: Iterable[Status]) -> Status:
    """Aggregate check outcomes without ever inventing a pass.

    ``adverse`` dominates. An ``unknown`` or ``not_checked`` in the set can
    never aggregate to ``clear``: the group is unknown until it is closed.
    """
    collected = [Status(item) for item in statuses]
    if not collected:
        return Status.NOT_CHECKED
    if any(item is Status.ADVERSE for item in collected):
        return Status.ADVERSE
    if any(item is Status.UNKNOWN for item in collected):
        return Status.UNKNOWN
    if any(item is Status.NOT_CHECKED for item in collected):
        return Status.NOT_CHECKED
    if all(item is Status.NOT_APPLICABLE for item in collected):
        return Status.NOT_APPLICABLE
    return Status.CLEAR


def weakest_confidence(values: Sequence[Confidence]) -> Confidence:
    if not values:
        return Confidence.UNKNOWN
    return min((Confidence(item) for item in values), key=lambda item: item.rank)


@dataclass
class Ledger:
    """Findings, limitations, and the rules that keep them honest."""

    findings: list[Finding] = field(default_factory=list)
    limitations: list[CoverageLimitation] = field(default_factory=list)

    def add(self, finding: Finding) -> Finding:
        if any(item.finding_id == finding.finding_id for item in self.findings):
            raise EvidenceError(f"duplicate finding id: {finding.finding_id}")
        self.findings.append(finding)
        return finding

    def limit(self, limitation: CoverageLimitation) -> CoverageLimitation:
        self.limitations.append(limitation)
        return limitation

    def by_surface(self, surface: str) -> list[Finding]:
        return [item for item in self.findings if item.surface == surface]

    def defects(self) -> list[str]:
        problems: list[str] = []
        for finding in self.findings:
            problems.extend(
                f"{finding.finding_id}: {problem}" for problem in finding.defects()
            )
        return problems

    def to_dict(self) -> dict[str, Any]:
        return {
            "findings": [item.to_dict() for item in self.findings],
            "coverage_limitations": [item.to_dict() for item in self.limitations],
        }
