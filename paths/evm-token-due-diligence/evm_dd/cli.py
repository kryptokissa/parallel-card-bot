"""Command line entry points.

Everything except ``packet`` runs offline, so the derivations and the
validator can be exercised, tested and demonstrated with no endpoint and
no credentials.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence

from evm_dd.addresses import AddressError, parse_target, to_checksum
from evm_dd import host
from evm_dd.evidence import Ledger
from evm_dd.manifest import (
    Declarations,
    build_manifest,
    load_json,
    validate_manifest,
    validate_report,
)
from evm_dd.pools import PoolKeyV4, v2_pair_address, v3_pool_address
from evm_dd.reconcile import Direction, Flow, FlowKind, Reconciliation
from evm_dd.rpc import ReadOnlyRpc, RpcUnavailable, WriteAttemptError
from evm_dd.selectors import describe_limits, scan_runtime


def _emit(payload: Any) -> int:
    json.dump(payload, sys.stdout, indent=2, sort_keys=False)
    sys.stdout.write("\n")
    return 0


def _open_rpc(args: argparse.Namespace, chain_id: int) -> tuple[ReadOnlyRpc, str]:
    """Resolve an endpoint: explicit flag, environment, then the host.

    A path installed on Wayfinder should work on its first run. The host
    already resolves a read endpoint per chain, so requiring the operator to
    supply one as well is a defect, not a safeguard.
    """
    explicit = (args.rpc or "").strip() or os.environ.get("EVM_RPC_URL", "").strip()
    if explicit:
        source = "--rpc" if (args.rpc or "").strip() else "EVM_RPC_URL"
        return ReadOnlyRpc(endpoint=explicit, chain_id=chain_id, timeout=args.timeout), source

    try:
        endpoint = host.resolve(chain_id)
    except host.HostUnavailable as exc:
        raise SystemExit(
            json.dumps(
                {
                    "ok": False,
                    "coverage_limitation": {
                        "scope": "rpc endpoint",
                        "reason": str(exc),
                        "consequence": (
                            "no read could be attempted, so nothing is known about "
                            "the target — this is a limit of the run, not a finding"
                        ),
                        "retryable": True,
                    },
                    "remedy": [
                        "pass --rpc <url>",
                        "set EVM_RPC_URL",
                        "or run inside the Wayfinder runtime, which provides one",
                    ],
                },
                indent=2,
            )
        ) from exc
    return (
        ReadOnlyRpc(
            endpoint=endpoint.url,
            chain_id=chain_id,
            timeout=args.timeout,
            headers=dict(endpoint.headers),
        ),
        endpoint.describe(),
    )


def cmd_packet(args: argparse.Namespace) -> int:
    from evm_dd.collect import build_packet, scan_executing_runtime

    try:
        target = parse_target(args.target)
    except AddressError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    ledger = Ledger()
    rpc, endpoint_source = _open_rpc(args, target.chain_id)
    try:
        packet = build_packet(
            rpc,
            target,
            ledger=ledger,
            decision_question=args.question or "",
            materiality=args.materiality or "",
            block=args.block,
        )
    except (RpcUnavailable, WriteAttemptError) as exc:
        limitation = getattr(exc, "limitation", None)
        print(
            json.dumps(
                {
                    "ok": False,
                    "coverage_limitation": limitation.to_dict() if limitation else str(exc),
                    "note": (
                        "This is a limit of the run, not a finding about the "
                        "token. Nothing may be marked clear because of it."
                    ),
                },
                indent=2,
            ),
            file=sys.stdout,
        )
        return 3
    scan = scan_executing_runtime(rpc, packet, ledger)
    digest, payload = packet.freeze()
    manifest = build_manifest(
        packet,
        declarations=Declarations(rpc_endpoints=[rpc.identity]),
        report_id=args.report_id or "",
    )
    result = {
        "ok": not packet.identity_defects(),
        "packet_digest": digest,
        "packet": payload,
        "capability_scan": scan,
        "manifest": manifest,
        "coverage_limitations": [item.to_dict() for item in ledger.limitations],
        "rpc": {**rpc.stats.to_dict(), "endpoint_source": endpoint_source},
    }
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"wrote {args.out}")
        return 0 if result["ok"] else 1
    _emit(result)
    return 0 if result["ok"] else 1


def cmd_report(args: argparse.Namespace) -> int:
    """Collect, rate, self-validate and emit — the whole pass in one command."""
    from evm_dd.assess import assess
    from evm_dd.collect import build_packet, scan_executing_runtime

    try:
        target = parse_target(args.target)
    except AddressError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    ledger = Ledger()
    rpc, endpoint_source = _open_rpc(args, target.chain_id)
    try:
        packet = build_packet(
            rpc,
            target,
            ledger=ledger,
            decision_question=args.question or "",
            materiality=(
                "Authority to mint, rewrite balances, seize, restrict transfers, make "
                "arbitrary calls or replace the code is material at any size."
            ),
            block=args.block,
        )
    except (RpcUnavailable, WriteAttemptError) as exc:
        limitation = getattr(exc, "limitation", None)
        _emit(
            {
                "ok": False,
                "coverage_limitation": limitation.to_dict() if limitation else str(exc),
                "note": (
                    "A limit of the run, not a finding about the token. No surface "
                    "may be rated clear because of it."
                ),
            }
        )
        return 3

    scan = scan_executing_runtime(rpc, packet, ledger)
    report = assess(
        packet,
        scan,
        ledger,
        question=args.question or "",
        requirement=args.requirement,
        mode=args.mode,
        source_id=args.report_id or f"{target.chain_id}-{target.address[:10]}",
    )
    payload = report.to_dict()
    manifest = build_manifest(
        packet,
        declarations=Declarations(rpc_endpoints=[rpc.identity]),
        report_id=report.source_id,
    )
    # The pack validates its own output before handing it over.
    validation = validate_report(manifest, payload)

    if args.out:
        base = Path(args.out)
        base.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        manifest_path = base.with_name(base.stem + "-manifest.json")
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(report.verdict.statement if report.verdict else "(no verdict)")
        print(f"\nwrote {base} and {manifest_path}")
        print(f"self-validation: {'PASS' if validation.ok else 'FAIL'}")
        for error in validation.errors:
            print(f"  error {error}")
        return 0 if validation.ok else 1

    _emit(
        {
            "ok": validation.ok,
            "report": payload,
            "manifest": manifest,
            "self_validation": validation.to_dict(),
            "rpc": {**rpc.stats.to_dict(), "endpoint_source": endpoint_source},
        }
    )
    return 0 if validation.ok else 1


def cmd_validate(args: argparse.Namespace) -> int:
    manifest = load_json(args.manifest)
    headers = load_json(args.headers) if args.headers else None
    if args.report:
        report = load_json(args.report)
        result = validate_report(manifest, report, headers=headers)
    else:
        result = validate_manifest(manifest)
    if args.json:
        _emit(result.to_dict())
    else:
        print(result.render())
    return 0 if result.ok else 1


def cmd_scan(args: argparse.Namespace) -> int:
    code = Path(args.code).read_text(encoding="utf-8").strip() if args.code else args.hex
    if not code:
        print("error: pass --code <file> or --hex <0x…>", file=sys.stderr)
        return 2
    scan = scan_runtime(code)
    payload = scan.to_dict()
    payload["caveats"] = describe_limits(scan)
    return _emit(payload)


def cmd_pool(args: argparse.Namespace) -> int:
    if args.version == "v4":
        key = PoolKeyV4(
            currency0=args.token0,
            currency1=args.token1,
            fee=args.fee,
            tick_spacing=args.tick_spacing,
            hooks=args.hooks,
        )
        payload = key.to_dict()
        payload["warnings"] = key.warnings()
        return _emit(payload)
    if not args.factory:
        print("error: v2/v3 derivation needs --factory", file=sys.stderr)
        return 2
    if args.version == "v3":
        address = v3_pool_address(args.factory, args.token0, args.token1, args.fee)
    else:
        address = v2_pair_address(args.factory, args.token0, args.token1)
    return _emit(
        {
            "version": args.version,
            "candidate_address": to_checksum(address),
            "note": (
                "This is a CREATE2 derivation, not an observation. Confirm the "
                "address holds code and reports the expected tokens before "
                "treating it as a pool, and confirm the factory and init-code "
                "hash belong to the deployment you mean — forks reuse names."
            ),
        }
    )


def cmd_reconcile(args: argparse.Namespace) -> int:
    payload = load_json(args.flows)
    reconciliation = Reconciliation(
        asset=payload["asset"],
        holder=payload["holder"],
        decimals=int(payload.get("decimals", 18)),
        opening=int(payload["opening"]),
        closing=int(payload["closing"]),
        from_block=int(payload["from_block"]),
        to_block=int(payload["to_block"]),
        tolerance=int(payload.get("tolerance", 0)),
    )
    for raw in payload.get("flows", []):
        reconciliation.add(
            Flow(
                label=raw["label"],
                amount=int(raw["amount"]),
                direction=Direction(raw["direction"]),
                kind=FlowKind(raw.get("kind", "transfer")),
                tx_hash=raw.get("tx_hash", ""),
                counterparty=raw.get("counterparty", ""),
                block_number=raw.get("block_number"),
                note=raw.get("note", ""),
                reverted=bool(raw.get("reverted", False)),
            )
        )
    result = reconciliation.to_dict()
    _emit(result)
    return 0 if reconciliation.is_closed else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="assay",
        description=(
            "Evidence-bounded diligence helpers for one EVM token. Read-only: "
            "this tool has no signing path and cannot broadcast."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    packet = sub.add_parser("packet", help="build a pinned target packet from an endpoint")
    packet.add_argument("target", help="eip155:<chainId>:<address> or <chainId>:<address>")
    packet.add_argument("--rpc", default="", help="JSON-RPC endpoint URL (default: EVM_RPC_URL, then the host runtime)")
    packet.add_argument("--block", default="latest", help="block tag to pin (default: latest)")
    packet.add_argument("--question", default="", help="the decision this run must answer")
    packet.add_argument("--materiality", default="", help="materiality rules for this run")
    packet.add_argument("--report-id", default="", help="identity to stamp on the report")
    packet.add_argument("--timeout", type=float, default=20.0)
    packet.add_argument("--out", default="", help="write the packet JSON here")
    packet.set_defaults(func=cmd_packet)

    report = sub.add_parser(
        "report", help="collect, rate and emit a self-validated report in one pass"
    )
    report.add_argument("target", help="eip155:<chainId>:<address> or <chainId>:<address>")
    report.add_argument("--rpc", default="", help="JSON-RPC endpoint URL (default: EVM_RPC_URL, then the host runtime)")
    report.add_argument("--block", default="latest", help="block tag to pin (default: latest)")
    report.add_argument("--question", default="", help="the decision this run must answer")
    report.add_argument(
        "--requirement",
        default="the token's own controls are not unilaterally dangerous",
        help="the standard the verdict is issued against",
    )
    report.add_argument("--mode", default="focused", choices=["focused", "broad", "formal"])
    report.add_argument("--report-id", default="")
    report.add_argument("--timeout", type=float, default=20.0)
    report.add_argument("--out", default="", help="write report and manifest here")
    report.set_defaults(func=cmd_report)

    validate = sub.add_parser("validate", help="validate a manifest, and optionally a report")
    validate.add_argument("manifest")
    validate.add_argument("--report", default="")
    validate.add_argument("--headers", default="", help="JSON of chainId -> block header")
    validate.add_argument("--json", action="store_true")
    validate.set_defaults(func=cmd_validate)

    scan = sub.add_parser("scan", help="capability scan of runtime bytecode")
    scan.add_argument("--code", default="", help="file containing runtime hex")
    scan.add_argument("--hex", default="", help="runtime hex inline")
    scan.set_defaults(func=cmd_scan)

    pool = sub.add_parser("pool", help="derive a pool address or a v4 PoolId")
    pool.add_argument("version", choices=["v2", "v3", "v4"])
    pool.add_argument("token0")
    pool.add_argument("token1")
    pool.add_argument("--factory", default="")
    pool.add_argument("--fee", type=int, default=3000)
    pool.add_argument("--tick-spacing", type=int, default=60)
    pool.add_argument("--hooks", default="0x0000000000000000000000000000000000000000")
    pool.set_defaults(func=cmd_pool)

    reconcile = sub.add_parser("reconcile", help="close a balance to an explicit residual")
    reconcile.add_argument("flows", help="JSON file describing the window")
    reconcile.set_defaults(func=cmd_reconcile)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
