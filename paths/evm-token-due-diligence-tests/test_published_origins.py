"""Nothing that ships may carry a URL literal.

A published path is scanned by the registry for origins it might contact.
The pack contacts nothing on its own — every endpoint is handed to it by
the operator at call time — so a hard-coded URL anywhere in the shipped
content is both wrong and a review rejection. v0.1.0 was held for exactly
that: an example in `simulation.md` spelled out a loopback RPC URL.

This test scans everything that goes into the bundle. Test code is not
scanned: it lives outside the path directory and is not published.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PACK_ROOT = Path(__file__).resolve().parents[1] / "evm-token-due-diligence"

# Mirrors the builder's own exclusions plus the artifacts it never ships.
EXCLUDED_DIRS = {".build", ".git", ".venv", ".wayfinder", "__pycache__", "dist", ".pytest_cache"}

URL_LITERAL = re.compile(r"https?://[^\s\"'`)\],]+")
LOOPBACK = re.compile(r"127\.0\.0\.1|\blocalhost\b|0\.0\.0\.0|\[::1\]|:8545")

# The one permitted mention of loopback hostnames: the refusal set that
# decides whether a fork endpoint may be used at all. It is a guard, not a
# destination, and removing it would remove the safety check.
ALLOWED_LOOPBACK_FILE = "evm_dd/rpc.py"


def _published_files() -> list[Path]:
    files = [
        path
        for path in PACK_ROOT.rglob("*")
        if path.is_file()
        and not any(part in EXCLUDED_DIRS for part in path.relative_to(PACK_ROOT).parts)
        and path.suffix not in {".zip", ".png", ".jpg"}
    ]
    assert len(files) >= 30, f"expected to scan the pack, found {len(files)} files"
    return files


def test_no_published_file_contains_a_url_literal():
    offenders: list[str] = []
    for path in _published_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            for match in URL_LITERAL.finditer(line):
                offenders.append(f"{path.relative_to(PACK_ROOT)}:{number}: {match.group(0)}")
    assert not offenders, (
        "published content must carry no URL literal — the operator supplies "
        "every endpoint at call time:\n  " + "\n  ".join(offenders)
    )


def test_loopback_hostnames_appear_only_in_the_refusal_guard():
    offenders: list[str] = []
    for path in _published_files():
        relative = path.relative_to(PACK_ROOT).as_posix()
        if relative == ALLOWED_LOOPBACK_FILE:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if LOOPBACK.search(line):
                offenders.append(f"{relative}:{number}: {line.strip()[:90]}")
    assert not offenders, (
        "loopback hostnames belong only in the fork-endpoint refusal guard in "
        f"{ALLOWED_LOOPBACK_FILE}:\n  " + "\n  ".join(offenders)
    )


def test_the_refusal_guard_still_refuses():
    """The point of keeping those hostnames is that they gate something."""
    import sys

    sys.path.insert(0, str(PACK_ROOT))
    from evm_dd.rpc import WriteAttemptError, fork_client, is_loopback_endpoint

    assert is_loopback_endpoint("http://127.0.0.1:8545")
    assert not is_loopback_endpoint("https://rpc.example.org")
    with pytest.raises(WriteAttemptError):
        fork_client("https://rpc.example.org")
