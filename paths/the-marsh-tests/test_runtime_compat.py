"""Runtime portability — the pack must not bind to one SDK release.

The Marsh shipped 0.1.0-0.1.4 pinned to wayfinder-paths==0.11.1, a
version that exists only in the SDK's git tree and was never published
to PyPI, so the thin runtime could never install it and every run
halted at bootstrap. These tests keep that class of failure from
coming back:

1. No module-level import of the SDK anywhere in the pack — every
   wayfinder_paths import is lazy (inside a function), so importing
   the engine, game, or component never requires the SDK at all.
2. Ghost hunts and game replay run on a bare interpreter.
3. The Solana submission helper (absent from published releases) is
   imported defensively, never at module scope.
"""

from __future__ import annotations

import ast
import os

ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "the-marsh")  # the path directory; tests sit beside it, not in it
PACK_SOURCES = ["engine", "game", "scripts", "strategy.py"]


def _pack_files():
    for entry in PACK_SOURCES:
        full = os.path.join(ROOT, entry)
        if os.path.isfile(full):
            yield full
        else:
            for base, _, files in os.walk(full):
                for fname in files:
                    if fname.endswith(".py"):
                        yield os.path.join(base, fname)


def _module_level_imports(tree: ast.Module):
    """Imports at module scope only — not those inside functions."""
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield node


def test_sdk_is_never_imported_at_module_scope():
    for path in _pack_files():
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for node in _module_level_imports(tree):
            names = ([a.name for a in node.names]
                     if isinstance(node, ast.Import) else [node.module or ""])
            for name in names:
                assert not name.startswith("wayfinder_paths"), (
                    f"{os.path.relpath(path, ROOT)} imports {name} at module "
                    f"scope: the pack must import the SDK lazily so it loads "
                    f"on any runtime"
                )


def test_solana_submission_import_is_guarded():
    """The one helper missing from published SDK releases must be
    wrapped so its absence degrades live Solana submission only."""
    src = open(os.path.join(ROOT, "engine", "executor.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for sub in ast.walk(node):
                if isinstance(sub, ast.ImportFrom) and \
                        "svm_transaction" in (sub.module or ""):
                    found.append(sub)
            continue
    assert found, "svm_transaction must be imported inside a try/except"
    assert "svm_transaction" in src
    # and the fallback must be a refusal, not a crash
    assert "solana submission unavailable" in src


def test_manifest_pins_a_published_runtime():
    """The pinned runtime must be a version that actually exists on
    PyPI (0.11.1 never shipped)."""
    manifest = open(os.path.join(ROOT, "wfpath.yaml"), encoding="utf-8").read()
    assert "version: 0.11.1" not in manifest, (
        "wayfinder-paths 0.11.1 is unpublished; pin a released version"
    )


def test_ghost_hunt_needs_no_sdk(tmp_path):
    from helpers import run_day

    engine, events = run_day(tmp_path, "calm_day.json", ghost=True)
    assert any(e["type"] == "ghost_shot" for e in events)


def test_practice_range_ships_with_the_pack():
    """The ghost hunt is the whole first-run experience, so it must not
    depend on anything the skill export strips.

    The export ships only the runtime tree (engine, game, scripts,
    strategy.py). `tests/` is left behind, so the practice marshes live
    in engine/practice.py and no runtime module may reach into tests/.
    """
    from engine import practice

    assert practice.MARSHES, "no practice marshes ship with the pack"
    assert practice.DEFAULT in practice.MARSHES
    assert practice.load()["scout"], "the default marsh has no ducks on it"

    for path in _pack_files():
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        assert "tests/fixtures" not in src, (
            f"{os.path.relpath(path, ROOT)} reads from tests/, which is "
            f"not shipped to installed players"
        )
