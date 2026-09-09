"""Command line entry points.

Everything except ``packet`` runs offline, so the derivations and the
validator can be exercised, tested and demonstrated with no endpoint and
no credentials.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from evm_dd.addresses import AddressError, parse_target, to_checksum
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


def cmd_packet(args: argparse.Namespace) -> int:
    from evm_dd.collect import build_packet, scan_executing_runtime

    try:
        target = parse_target(args.target)
    except AddressError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    ledger = Ledger()
    rpc = ReadOnlyRpc(endpoint=args.rpc, chain_id=target.chain_id, timeout=args.timeout)
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
        "rpc": rpc.stats.to_dict(),
    }
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"wrote {args.out}")
        return 0 if result["ok"] else 1
    _emit(result)
    return 0 if result["ok"] else 1


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
    packet.add_argument("--rpc", required=True, help="JSON-RPC endpoint URL")
    packet.add_argument("--block", default="latest", help="block tag to pin (default: latest)")
    packet.add_argument("--question", default="", help="the decision this run must answer")
    packet.add_argument("--materiality", default="", help="materiality rules for this run")
    packet.add_argument("--report-id", default="", help="identity to stamp on the report")
    packet.add_argument("--timeout", type=float, default=20.0)
    packet.add_argument("--out", default="", help="write the packet JSON here")
    packet.set_defaults(func=cmd_packet)

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
