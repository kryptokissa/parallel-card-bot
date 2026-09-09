"""The read-only guarantee, enforced rather than promised.

A reviewer's first question about anything that touches a chain is "can
this move my money?". These tests answer it structurally: there is no
signing path, no key-material identifier, and no reachable write method.

The loopback addresses below are **non-runtime local test fixtures**: they
exercise the refusal check that decides whether a fork endpoint is
acceptable. Nothing here is contacted — every transport in this suite is a
scripted stub — and this file is not part of the published pack. See
`test_published_origins.py`, which enforces that no URL literal reaches
shipped content.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from evm_dd.rpc import (
    FORBIDDEN_METHODS,
    FORK_METHODS,
    READ_METHODS,
    ReadOnlyRpc,
    WriteAttemptError,
    endpoint_identity,
    fork_client,
    is_loopback_endpoint,
)
from helpers import scripted_transport

PACK_ROOT = Path(__file__).resolve().parents[1] / "evm-token-due-diligence"

BANNED_IMPORTS = {
    "eth_account",
    "eth_keys",
    "eth_keyfile",
    "coincurve",
    "ecdsa",
    "keyring",
    "mnemonic",
    "bip32",
    "bip_utils",
    "hdwallet",
}

BANNED_IDENTIFIERS = {
    "private_key",
    "privatekey",
    "privkey",
    "mnemonic",
    "seed_phrase",
    "seedphrase",
    "keystore",
    "sign_transaction",
    "sign_message",
    "signtypeddata",
    "send_raw_transaction",
    "unlock_account",
    "from_key",
}


def _python_files() -> list[Path]:
    files = [
        path
        for path in PACK_ROOT.rglob("*.py")
        if ".build" not in path.parts and "__pycache__" not in path.parts
    ]
    # The suite moved beside the path; if PACK_ROOT ever stops resolving, these
    # scans would pass by scanning nothing at all.
    assert len(files) >= 15, f"expected to scan the pack, found {len(files)} files"
    return files


def test_the_pack_imports_no_signing_or_key_library():
    offenders: list[str] = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                root = name.split(".")[0]
                if root in BANNED_IMPORTS:
                    offenders.append(f"{path.relative_to(PACK_ROOT)}: imports {name}")
    assert not offenders, offenders


def test_no_key_material_identifier_exists_anywhere_in_the_pack():
    offenders: list[str] = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            name = None
            if isinstance(node, ast.Name):
                name = node.id
            elif isinstance(node, ast.Attribute):
                name = node.attr
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = node.name
            elif isinstance(node, ast.arg):
                name = node.arg
            if name and name.lower().replace("__", "") in BANNED_IDENTIFIERS:
                offenders.append(f"{path.relative_to(PACK_ROOT)}: identifier {name}")
    assert not offenders, offenders


def test_the_pack_depends_only_on_the_standard_library():
    third_party = {"web3", "requests", "httpx", "aiohttp", "eth_utils", "eth_abi"}
    offenders: list[str] = []
    for path in _python_files():
        if path.parts[-2] == "tests":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            offenders.extend(
                f"{path.relative_to(PACK_ROOT)}: imports {name}"
                for name in names
                if name.split(".")[0] in third_party
            )
    assert not offenders, offenders


@pytest.mark.parametrize("method", sorted(FORBIDDEN_METHODS))
def test_signing_and_account_methods_are_never_reachable(method):
    client = ReadOnlyRpc(endpoint="https://rpc.example.org", transport=scripted_transport(lambda m, p: "0x1"))
    with pytest.raises(WriteAttemptError):
        client.call(method, [])
    fork = fork_client("http://127.0.0.1:8545", transport=scripted_transport(lambda m, p: "0x1"))
    with pytest.raises(WriteAttemptError):
        fork.call(method, [])


@pytest.mark.parametrize("method", sorted(FORK_METHODS))
def test_state_changing_methods_need_an_explicit_fork_client(method):
    client = ReadOnlyRpc(endpoint="https://rpc.example.org", transport=scripted_transport(lambda m, p: "0x1"))
    with pytest.raises(WriteAttemptError) as excinfo:
        client.call(method, [])
    assert "counterfactual" in str(excinfo.value)


def test_fork_mode_refuses_a_remote_endpoint():
    with pytest.raises(WriteAttemptError):
        fork_client("https://mainnet.example.org")
    assert is_loopback_endpoint("http://127.0.0.1:8545")
    assert is_loopback_endpoint("http://localhost:8545")
    assert not is_loopback_endpoint("https://rpc.example.org")


def test_unlisted_methods_are_refused_by_default():
    client = ReadOnlyRpc(endpoint="https://rpc.example.org", transport=scripted_transport(lambda m, p: "0x1"))
    with pytest.raises(WriteAttemptError):
        client.call("eth_someNewThing", [])


def test_read_and_write_method_sets_do_not_overlap():
    assert not (READ_METHODS & FORK_METHODS)
    assert not (READ_METHODS & FORBIDDEN_METHODS)


def test_endpoint_identity_never_carries_the_credential():
    identity = endpoint_identity("https://user:hunter2@eth.example.org/v2/0123456789abcdef0123456789abcdef")
    assert "hunter2" not in identity
    assert "0123456789abcdef" not in identity
    assert "eth.example.org" in identity
