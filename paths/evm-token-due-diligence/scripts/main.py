"""Path component: read-only views over the pack, and the CLI front door.

Resolves the pack root from this file's own location, so the component
works from a checkout, from an installed path, and from a rendered skill
export without anything being configured.

Every view here is read-only. The component holds no keys, signs nothing,
and cannot broadcast — see ``evm_dd/rpc.py`` for where that is enforced
and ``tests/test_read_only.py`` for where it is proved.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PACK_ROOT = Path(__file__).resolve().parents[1]
if str(PACK_ROOT) not in sys.path:
    sys.path.insert(0, str(PACK_ROOT))

from evm_dd.cli import build_parser, main as cli_main  # noqa: E402
from evm_dd.examples import EXAMPLES, SYNTHETIC_NOTICE, build  # noqa: E402
from evm_dd.manifest import VALIDATOR_SCOPE  # noqa: E402
from evm_dd.report import DIMENSIONS  # noqa: E402
from evm_dd.rpc import FORBIDDEN_METHODS, FORK_METHODS, READ_METHODS  # noqa: E402
from evm_dd.selectors import ALWAYS_MATERIAL, KNOWN_SELECTORS  # noqa: E402


def state_snapshot() -> dict:
    """What the applet renders when it has no report loaded yet."""
    return {
        "path": "evm-token-due-diligence",
        "component": "main",
        "mode": "read_only",
        "capabilities": {
            "read_methods": len(READ_METHODS),
            "fork_only_methods": len(FORK_METHODS),
            "forbidden_methods": sorted(FORBIDDEN_METHODS),
            "known_selectors": len(KNOWN_SELECTORS),
            "always_material_capabilities": sorted(ALWAYS_MATERIAL),
        },
        "declarations": {
            "no_real_signing": True,
            "no_broadcast": True,
            "no_key_material": True,
            "simulation": "fork-only, loopback-verified, synthetic accounts",
        },
        "dimensions": [
            {"id": name, "description": description} for name, description in DIMENSIONS
        ],
        "examples": sorted(EXAMPLES),
        "validator_scope": VALIDATOR_SCOPE,
        "synthetic_notice": SYNTHETIC_NOTICE,
    }


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("state", "--state"):
        json.dump(state_snapshot(), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    if args[0] == "example":
        if len(args) < 2:
            json.dump({"examples": sorted(EXAMPLES)}, sys.stdout, indent=2)
            sys.stdout.write("\n")
            return 0
        try:
            payload = build(args[1])
        except KeyError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    if args[0] in ("-h", "--help", "help"):
        build_parser().print_help()
        print(
            "\nComponent views:\n"
            "  state             read-only snapshot of the pack\n"
            "  example [<id>]    render one synthetic worked example",
        )
        return 0
    return cli_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
