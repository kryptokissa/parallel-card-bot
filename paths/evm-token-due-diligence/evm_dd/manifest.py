"""The target-integrity manifest and the report validator.

The manifest is a small, signed-by-nothing record of *what was actually
examined*: the requested target, the chain the endpoint answered for, the
pins with their header digests, the scope addresses with role and
provenance, and the run's declarations about writing and signing.

The validator then checks a finished report against it. It is a
consistency check, and it is worth being precise about what that means:

    Passing says the report is about the target it claims to be about,
    that its pins are real and self-consistent, that its scope addresses
    are attributed, that nothing unknown is presented as a pass, and that
    the run declared no signing or broadcasting.

    Passing does NOT say the RPC endpoint told the truth, that pool or
    holder discovery was complete, that the analysis was competent, or
    that the token is safe.

A validator that oversold itself would be the same failure it exists to
catch.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from evm_dd.addresses import is_address, normalize, to_checksum
from evm_dd.evidence import Status, worst_status
from evm_dd.pins import BlockPin, PinError
from evm_dd.target import METADATA_FIELDS, TargetPacket

MANIFEST_VERSION = "1"

# Duplicated from evm_dd.report rather than imported, so the validator stays
# usable standalone with no dependency on report assembly. A test asserts the
# two lists cannot drift apart.
REQUIRED_DIMENSIONS: tuple[str, ...] = (
    "token_controls",
    "canonical_lp_custody",
    "side_pool_removal_risk",
    "sellability_and_depth",
    "current_concentration",
    "launch_integrity",
    "admin_treasury_custody",
    "reward_accounting",
    "utility_and_redemption",
    "external_dependencies",
    "development_and_disclosure",
)

VALIDATOR_SCOPE = (
    "Passing validates internal consistency only: that the report is about "
    "the manifest's target, that its pins are well formed and match their "
    "captured headers, that material scope addresses carry a chain, role, "
    "provenance and runtime status, that no unknown or skipped check is "
    "presented as a pass, and that the run declared no real signing or "
    "broadcasting. It does not validate RPC honesty, discovery "
    "completeness, decoding correctness, or the safety of the token."
)

# Words that assert unconditional safety. A diligence verdict is always
# bounded by a block, a size and a coverage statement.
_ABSOLUTE_SAFETY_RE = re.compile(
    r"(?i)\b(is safe|are safe|完全|totally safe|risk[- ]free|no risk|zero risk|"
    r"cannot be rugged|rug[- ]proof|guaranteed|fully audited|100% safe)\b"
)
_BOUNDING_RE = re.compile(
    r"(?i)(at the pinned block|at block \d|as of block|under the tested|"
    r"at the tested|at the quoted|within the searched|no current |"
    r"no executable |not found at|unknown because|under the stated)"
)


class ManifestError(ValueError):
    pass


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "checks_run": list(self.checked),
            "validator_scope": VALIDATOR_SCOPE,
        }

    def render(self) -> str:
        lines = ["PASS" if self.ok else "FAIL"]
        for item in self.errors:
            lines.append(f"  error   {item}")
        for item in self.warnings:
            lines.append(f"  warning {item}")
        lines.append(f"  checks  {len(self.checked)} run")
        lines.append(f"  scope   {VALIDATOR_SCOPE}")
        return "\n".join(lines)


@dataclass
class Declarations:
    """What the run promises about its own behaviour, in machine-checkable form."""

    no_real_signing: bool = True
    no_broadcast: bool = True
    no_key_material: bool = True
    simulation: str = "none"  # "none" | "fork"
    fork_endpoint_loopback: bool | None = None
    fork_accounts: str = "synthetic"
    rpc_endpoints: list[str] = field(default_factory=list)

    def defects(self) -> list[str]:
        problems: list[str] = []
        if not (self.no_real_signing and self.no_broadcast and self.no_key_material):
            problems.append(
                "this pack has no signing path; a run that declares signing, "
                "broadcasting or key access is misconfigured, not permitted"
            )
        if self.simulation not in ("none", "fork"):
            problems.append(f"unknown simulation mode: {self.simulation!r}")
        if self.simulation == "fork":
            if self.fork_endpoint_loopback is not True:
                problems.append(
                    "fork simulation is only permitted against a verified "
                    "disposable local fork (loopback endpoint)"
                )
            if self.fork_accounts != "synthetic":
                problems.append(
                    "fork simulation must use synthetic test accounts"
                )
        return problems

    def to_dict(self) -> dict[str, Any]:
        return {
            "no_real_signing": self.no_real_signing,
            "no_broadcast": self.no_broadcast,
            "no_key_material": self.no_key_material,
            "simulation": self.simulation,
            "fork_endpoint_loopback": self.fork_endpoint_loopback,
            "fork_accounts": self.fork_accounts,
            "rpc_endpoints": list(self.rpc_endpoints),
        }


def build_manifest(
    packet: TargetPacket,
    *,
    declarations: Declarations | None = None,
    report_id: str = "",
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Derive the integrity manifest from a frozen target packet."""
    digest, payload = packet.freeze()
    declarations = declarations or Declarations()
    pins = {str(packet.pin.chain_id): packet.pin.to_dict()}
    for chain_id, pin in packet.additional_pins.items():
        pins[str(chain_id)] = pin.to_dict()
    return {
        "manifest_version": MANIFEST_VERSION,
        "generated_at": generated_at
        or datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z"),
        "target": {
            "requested_chain_id": packet.requested.chain_id,
            "requested_address": to_checksum(packet.requested.address),
            "observed_chain_id": packet.observed_chain_id,
            "caip10": packet.requested.caip10,
        },
        "metadata": payload["metadata"],
        "unresolved_metadata": payload["unresolved_metadata"],
        "pins": pins,
        "header_digests": {str(packet.pin.chain_id): packet.header_digest()},
        "runtime": {
            "hash": packet.runtime_hash,
            "size": packet.runtime_size,
            "executing_address": (
                (packet.proxy or {}).get("executing_address")
                or to_checksum(packet.requested.address)
            ),
            "proxy_pattern": (packet.proxy or {}).get("pattern", "none"),
        },
        "scope": payload["scope"],
        "declarations": declarations.to_dict(),
        "report_source": {"id": report_id, "packet_digest": digest},
        "known_limitations": list(packet.known_limitations),
    }


# ---------------------------------------------------------------------------
# validation


def _pin_from_manifest(raw: Mapping[str, Any]) -> BlockPin:
    return BlockPin.from_dict(raw)


def _check_addresses(
    label: str, values: Iterable[Any], errors: list[str]
) -> None:
    for value in values:
        if not is_address(str(value)):
            errors.append(f"{label}: malformed address {value!r}")


def validate_manifest(manifest: Mapping[str, Any]) -> ValidationResult:
    """Check the manifest can stand on its own before comparing a report."""
    errors: list[str] = []
    warnings: list[str] = []
    checked: list[str] = []

    if str(manifest.get("manifest_version")) != MANIFEST_VERSION:
        errors.append(
            f"manifest_version must be {MANIFEST_VERSION!r}, got "
            f"{manifest.get('manifest_version')!r}"
        )
    checked.append("manifest version")

    target = manifest.get("target") or {}
    requested_chain = target.get("requested_chain_id")
    observed_chain = target.get("observed_chain_id")
    requested_address = str(target.get("requested_address") or "")
    if not is_address(requested_address):
        errors.append(f"target.requested_address is malformed: {requested_address!r}")
    if not isinstance(requested_chain, int) or requested_chain <= 0:
        errors.append(f"target.requested_chain_id is not a chain id: {requested_chain!r}")
    if observed_chain != requested_chain:
        errors.append(
            f"identity conflict: requested chain {requested_chain} but the "
            f"endpoint answered for chain {observed_chain}. A same-symbol "
            "token on another chain is a different token."
        )
    checked.append("requested vs observed chain")

    pins = manifest.get("pins") or {}
    if not pins:
        errors.append("manifest has no pins; current state must be pinned")
    for chain_key, raw in pins.items():
        try:
            pin = _pin_from_manifest(raw)
        except PinError as exc:
            errors.append(f"pin for chain {chain_key}: {exc}")
            continue
        if str(pin.chain_id) != str(chain_key):
            errors.append(
                f"pin filed under chain {chain_key} declares chain {pin.chain_id}"
            )
    checked.append("pins well formed")

    if str(requested_chain) not in {str(key) for key in pins}:
        errors.append(
            f"no pin for the target's own chain ({requested_chain})"
        )
    checked.append("target chain is pinned")

    metadata = manifest.get("metadata") or {}
    for name in METADATA_FIELDS:
        entry = metadata.get(name)
        if entry is None:
            errors.append(
                f"metadata.{name} is absent; it must be present as resolved or "
                "explicitly unresolved, never omitted"
            )
            continue
        state = str(entry.get("state"))
        if state not in ("resolved", "unresolved"):
            errors.append(f"metadata.{name} has no explicit state")
        if state == "resolved" and entry.get("value") in (None, ""):
            errors.append(f"metadata.{name} is marked resolved with an empty value")
        if state == "unresolved" and not str(entry.get("reason") or "").strip():
            errors.append(f"metadata.{name} is unresolved without a reason")
    checked.append("metadata resolved or explicitly unresolved")

    scope = manifest.get("scope") or []
    for entry in scope:
        address = str(entry.get("address") or "")
        if not is_address(address):
            errors.append(f"scope: malformed address {address!r}")
            continue
        if not entry.get("role"):
            errors.append(f"scope {address}: missing role")
        if not entry.get("provenance"):
            errors.append(f"scope {address}: missing provenance")
        chain_id = entry.get("chain_id")
        if not isinstance(chain_id, int) or chain_id <= 0:
            errors.append(f"scope {address}: missing or invalid chain_id")
        elif str(chain_id) not in {str(key) for key in pins}:
            errors.append(
                f"scope {address}: on chain {chain_id}, which has no pin in "
                "this manifest"
            )
        if entry.get("material", True) and entry.get("runtime_status") == "unknown":
            warnings.append(
                f"scope {address}: runtime status unknown — conclusions that "
                "depend on it cannot be settled"
            )
    checked.append("scope addresses attributed and on pinned chains")

    declarations = manifest.get("declarations") or {}
    declared = Declarations(
        no_real_signing=bool(declarations.get("no_real_signing")),
        no_broadcast=bool(declarations.get("no_broadcast")),
        no_key_material=bool(declarations.get("no_key_material", True)),
        simulation=str(declarations.get("simulation", "none")),
        fork_endpoint_loopback=declarations.get("fork_endpoint_loopback"),
        fork_accounts=str(declarations.get("fork_accounts", "synthetic")),
    )
    errors.extend(declared.defects())
    checked.append("no-signing / no-broadcast / fork restrictions")

    return ValidationResult(
        ok=not errors, errors=errors, warnings=warnings, checked=checked
    )


def validate_report(
    manifest: Mapping[str, Any],
    report: Mapping[str, Any],
    *,
    headers: Mapping[str, Mapping[str, Any]] | None = None,
) -> ValidationResult:
    """Validate a finished report against the manifest it claims to come from.

    ``headers`` optionally maps chain id (as a string) to a freshly fetched
    block header; when supplied, each pin is re-checked against it.
    """
    result = validate_manifest(manifest)
    errors = list(result.errors)
    warnings = list(result.warnings)
    checked = list(result.checked)

    target = manifest.get("target") or {}
    manifest_address = str(target.get("requested_address") or "")
    manifest_chain = target.get("requested_chain_id")

    reported = report.get("target") or {}
    reported_address = str(reported.get("address") or reported.get("requested_address") or "")
    reported_chain = reported.get("chain_id", reported.get("requested_chain_id"))

    if not is_address(reported_address):
        errors.append(f"report target address is malformed: {reported_address!r}")
    elif not is_address(manifest_address):
        pass  # already reported by validate_manifest
    elif normalize(reported_address) != normalize(manifest_address):
        errors.append(
            "report is about a different address than the manifest: "
            f"{to_checksum(reported_address)} vs {to_checksum(manifest_address)}"
        )
    if reported_chain != manifest_chain:
        errors.append(
            f"report is about chain {reported_chain}, manifest target is on "
            f"chain {manifest_chain}"
        )
    checked.append("report target matches manifest target")

    source = report.get("source") or {}
    manifest_source = manifest.get("report_source") or {}
    if manifest_source.get("packet_digest") and source.get("packet_digest") != manifest_source.get("packet_digest"):
        errors.append(
            "report packet digest does not match the manifest: the report was "
            "built from a different target packet"
        )
    if manifest_source.get("id") and source.get("id") != manifest_source.get("id"):
        errors.append(
            f"report source id {source.get('id')!r} does not match the "
            f"manifest's {manifest_source.get('id')!r}"
        )
    checked.append("report source identity")

    manifest_pins = manifest.get("pins") or {}
    report_pins = report.get("pins") or {}
    if str(manifest_chain) not in {str(key) for key in report_pins}:
        errors.append(
            f"report carries no pin for its own target chain ({manifest_chain}); "
            "current state is only current relative to a block"
        )
    for chain_key, raw in report_pins.items():
        try:
            pin = _pin_from_manifest(raw)
        except PinError as exc:
            errors.append(f"report pin for chain {chain_key}: {exc}")
            continue
        reference = manifest_pins.get(str(chain_key))
        if reference is None:
            errors.append(
                f"report pins chain {chain_key}, which is not in the manifest"
            )
            continue
        if str(reference.get("block_hash", "")).lower() != pin.block_hash:
            errors.append(
                f"chain {chain_key}: report pin {pin.block_hash} does not match "
                f"manifest pin {reference.get('block_hash')}"
            )
        if int(reference.get("number", -1)) != pin.number:
            errors.append(
                f"chain {chain_key}: report pins block {pin.number}, manifest "
                f"pins block {reference.get('number')}"
            )
    checked.append("report pins match manifest pins")

    for chain_key, header in (headers or {}).items():
        raw = manifest_pins.get(str(chain_key))
        if raw is None:
            errors.append(f"header supplied for unpinned chain {chain_key}")
            continue
        try:
            pin = _pin_from_manifest(raw)
        except PinError as exc:
            errors.append(f"pin for chain {chain_key}: {exc}")
            continue
        problems = pin.verify_against_header(header)
        errors.extend(
            f"chain {chain_key}: pin disagrees with the fetched header ({item})"
            for item in problems
        )
    if headers:
        checked.append("pins re-checked against fetched headers")

    # Ratings: no unknown may be presented as a pass.
    for rating in report.get("ratings") or []:
        dimension = rating.get("dimension", "<unnamed>")
        declared_status = rating.get("status")
        try:
            declared = Status(str(declared_status))
        except ValueError:
            errors.append(f"rating {dimension}: unknown status {declared_status!r}")
            continue
        raw_checks = rating.get("check_statuses")
        if raw_checks:
            try:
                implied = worst_status(Status(str(item)) for item in raw_checks)
            except ValueError:
                errors.append(f"rating {dimension}: a check status is not a Status value")
                continue
            if declared is not implied:
                errors.append(
                    f"rating {dimension}: declared {declared.value} but its own "
                    f"checks aggregate to {implied.value} — an unknown or "
                    "skipped check is being presented as a settled result"
                )
        elif declared.is_settled:
            warnings.append(
                f"rating {dimension}: settled as {declared.value} without "
                "listing the checks behind it"
            )
        if declared.is_favourable and not str(rating.get("coverage") or "").strip():
            errors.append(
                f"rating {dimension}: a favourable rating must state its coverage"
            )
    checked.append("no unknown presented as a pass")

    # Findings must cite evidence.
    for finding in report.get("findings") or []:
        finding_id = finding.get("finding_id", "<unidentified>")
        status = str(finding.get("status", ""))
        if finding.get("material", True) and status in ("clear", "adverse"):
            if not finding.get("evidence"):
                errors.append(f"finding {finding_id}: material and settled but cites no evidence")
        finding_chain = finding.get("chain_id")
        if finding_chain is not None and str(finding_chain) not in {
            str(key) for key in manifest_pins
        }:
            errors.append(
                f"finding {finding_id}: on chain {finding_chain}, which has no "
                "pin in this manifest"
            )
        address = finding.get("address")
        if address is not None and not is_address(str(address)):
            errors.append(f"finding {finding_id}: malformed address {address!r}")
    checked.append("findings cite evidence and stay on pinned chains")

    verdict = report.get("verdict") or {}
    call = str(verdict.get("call") or "").upper()
    if call == "GO":
        # A favourable call is a statement about every surface, so every
        # surface has to have been settled. This is rule 7 applied one level
        # up: the verdict cannot be cleaner than the ratings under it.
        rated = {str(item.get("dimension")): item for item in report.get("ratings") or []}
        unsettled = sorted(
            name
            for name, item in rated.items()
            if str(item.get("status")) not in ("clear", "not_applicable")
        )
        missing_dimensions = [name for name in REQUIRED_DIMENSIONS if name not in rated]
        if unsettled or missing_dimensions:
            errors.append(
                "verdict is GO while "
                + ", ".join(unsettled + missing_dimensions)
                + " is unsettled or unrated — a favourable call may not rest on "
                "surfaces that were never closed"
            )
    checked.append("a favourable verdict rests only on settled surfaces")

    statement = str(verdict.get("statement") or "")
    if not statement.strip():
        errors.append("report has no verdict statement")
    else:
        if _ABSOLUTE_SAFETY_RE.search(statement):
            errors.append(
                "verdict asserts unconditional safety; a diligence verdict is "
                "bounded by a block, a tested size and a coverage statement"
            )
        if not _BOUNDING_RE.search(statement):
            warnings.append(
                "verdict statement carries no explicit bound (block, tested "
                "size, or coverage) — bounded language is expected"
            )
    checked.append("verdict is conditional")

    return ValidationResult(
        ok=not errors, errors=errors, warnings=warnings, checked=checked
    )


def load_json(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ManifestError(f"{path} must contain a JSON object")
    return payload
