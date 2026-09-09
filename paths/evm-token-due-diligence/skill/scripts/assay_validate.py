#!/usr/bin/env python3
"""Standalone validator for a diligence manifest and report.

Portable on purpose: standard library only, no installation, and the pack
root is resolved from this file's own location so it runs from a checkout,
from an installed path, or from a rendered skill export.

    python scripts/assay_validate.py manifest.json --report report.json
    python scripts/assay_validate.py manifest.json --headers headers.json --json

Exit status is 0 when the documents are internally consistent and 1 when
they are not. Consistency is not safety — the printed scope note says
exactly what a pass does and does not mean.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _find_pack_root() -> Path:
    """Locate the directory containing the ``evm_dd`` package.

    Layouts this must cover, without any of them being configured:
      <pack>/skill/scripts/assay_validate.py   (repository)
      <export>/scripts/assay_validate.py       (rendered skill export,
                                                package under <export>/path)
    """
    here = Path(__file__).resolve()
    candidates = []
    for parent in here.parents:
        candidates.append(parent)
        candidates.append(parent / "path")
    for candidate in candidates:
        if (candidate / "evm_dd" / "manifest.py").is_file():
            return candidate
    raise SystemExit(
        "could not locate the evm_dd package relative to this script; run it "
        "from inside the path directory or its rendered skill export"
    )


sys.path.insert(0, str(_find_pack_root()))

from evm_dd.manifest import validate_manifest, validate_report  # noqa: E402


def _load(path: str) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"{path} must contain a JSON object")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate a diligence report against its target-integrity manifest."
    )
    parser.add_argument("manifest")
    parser.add_argument("--report", default="", help="report JSON to check against the manifest")
    parser.add_argument(
        "--headers",
        default="",
        help="JSON object of chainId -> freshly fetched block header",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    manifest = _load(args.manifest)
    if args.report:
        headers = _load(args.headers) if args.headers else None
        result = validate_report(manifest, _load(args.report), headers=headers)
    else:
        result = validate_manifest(manifest)

    if args.json:
        json.dump(result.to_dict(), sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print(result.render())
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
