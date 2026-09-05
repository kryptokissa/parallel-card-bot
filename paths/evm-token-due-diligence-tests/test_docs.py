"""Documentation has to hold together too.

Progressive disclosure only works if the links resolve, and a rendered
skill export only works if its paths are valid inside the export — not
inside this repository.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from evm_dd.examples import EXAMPLES

PACK_ROOT = Path(__file__).resolve().parents[1] / "evm-token-due-diligence"
SKILL_DIR = PACK_ROOT / "skill"
INSTRUCTIONS = SKILL_DIR / "instructions.md"
REFERENCES = SKILL_DIR / "references"

# The doctor rejects these in skill docs: they only work in a checkout of
# the tooling repository, not in a rendered export.
REPO_NATIVE = re.compile(r"\b(poetry run|python -m wayfinder_paths)\b")
# And these escape the export root.
PATH_ESCAPE = re.compile(r"(^|[\s`])(\.\./|/Users/|/home/)")


def _skill_docs() -> list[Path]:
    return [INSTRUCTIONS, *sorted(REFERENCES.glob("*.md"))]


def test_every_reference_the_instructions_link_to_exists():
    text = INSTRUCTIONS.read_text(encoding="utf-8")
    linked = set(re.findall(r"references/([a-z0-9\-]+\.md)", text))
    assert linked, "the instructions should route to references"
    missing = [name for name in sorted(linked) if not (REFERENCES / name).is_file()]
    assert not missing, missing


def test_no_reference_is_orphaned():
    corpus = "\n".join(path.read_text(encoding="utf-8") for path in _skill_docs())
    orphans = [
        path.name
        for path in sorted(REFERENCES.glob("*.md"))
        if f"references/{path.name}" not in corpus
    ]
    assert not orphans, orphans


@pytest.mark.parametrize("path", _skill_docs(), ids=lambda p: p.name)
def test_skill_docs_carry_no_repo_native_commands_or_path_escapes(path):
    text = path.read_text(encoding="utf-8")
    assert not REPO_NATIVE.search(text), f"{path.name}: repo-native command"
    assert not PATH_ESCAPE.search(text), f"{path.name}: path outside the export root"


def test_examples_named_in_the_docs_actually_exist():
    corpus = "\n".join(path.read_text(encoding="utf-8") for path in _skill_docs())
    named = set(re.findall(r"`([a-z0-9\-]{10,})`", corpus))
    referenced = {name for name in named if name in EXAMPLES or name.count("-") >= 3}
    unknown = [
        name
        for name in sorted(referenced)
        if name not in EXAMPLES and name.endswith(("-pool", "-degradation", "-layer", "-rebought", "-claims", "-limit"))
    ]
    assert not unknown, unknown


def test_the_docs_state_that_a_pass_is_not_a_safety_claim():
    """The caveat has to be written down where a reader will meet it.

    Linting prose for the word "safe" would be noise — the docs discuss
    safety claims in order to refuse them. What matters is that both the
    operating instructions and the README say plainly what passing does not
    mean, and the validator enforces the same rule on verdict statements.
    """
    for path in [INSTRUCTIONS, PACK_ROOT / "README.md"]:
        text = path.read_text(encoding="utf-8").lower()
        assert "not that the endpoint was honest" in text or "does not mean" in text, path.name
        assert "safe" in text, path.name

    from evm_dd.manifest import _ABSOLUTE_SAFETY_RE

    assert _ABSOLUTE_SAFETY_RE.search("this token is safe")
    assert _ABSOLUTE_SAFETY_RE.search("rug-proof and risk free".replace("risk free", "risk-free"))
    assert not _ABSOLUTE_SAFETY_RE.search(
        "No current executable removal path found at the pinned block."
    )


def test_the_applet_ships_every_asset_it_references_and_no_service_worker():
    index = PACK_ROOT / "applet" / "dist" / "index.html"
    html = index.read_text(encoding="utf-8")
    assert "serviceWorker" not in html
    assert not re.search(r"""(?:src|href)=["']/(assets|_next)/""", html)
    for reference in re.findall(r"""(?:src|href)=["']\./([^"']+)["']""", html):
        assert (index.parent / reference).is_file(), reference
    assert "data-path-ready" in html


def test_the_applet_never_posts_to_a_wildcard_origin():
    app = (PACK_ROOT / "applet" / "dist" / "assets" / "app.js").read_text(encoding="utf-8")
    assert 'postMessage(message, "*")' not in app
    assert "hostOrigin" in app


def test_the_readme_documents_every_cli_subcommand():
    from evm_dd.cli import build_parser

    readme = (PACK_ROOT / "README.md").read_text(encoding="utf-8")
    subparsers = [
        action
        for action in build_parser()._actions
        if hasattr(action, "choices") and isinstance(action.choices, dict)
    ]
    for name in subparsers[0].choices:
        assert f"main.py {name}" in readme, name


def test_the_validators_dimension_list_cannot_drift_from_the_reports():
    """`manifest.py` duplicates the surface list to stay standalone.

    Duplication is a deliberate trade for independence, which is only safe
    if drift is impossible.
    """
    from evm_dd.manifest import REQUIRED_DIMENSIONS
    from evm_dd.report import DIMENSION_IDS

    assert REQUIRED_DIMENSIONS == DIMENSION_IDS


EXAMPLE_ASSET_HEADER = (
    "/* Synthetic worked example, generated by `python path/scripts/main.py\n"
    " * example locked-canonical-removable-side-pool`. Every address, balance\n"
    " * and hash in it is invented. It exists so the page is legible before a\n"
    " * real report is loaded, and it is banner-labelled as synthetic when\n"
    " * rendered. Regenerate it with the same command; do not hand-edit. */\n"
)


def render_example_asset() -> str:
    """The single source of truth for the applet's embedded example."""
    import json

    from evm_dd.examples import build

    return (
        EXAMPLE_ASSET_HEADER
        + "window.__ASSAY_EXAMPLE__ = "
        + json.dumps(build("locked-canonical-removable-side-pool"), indent=1)
        + ";\n"
    )


def test_the_applets_embedded_example_matches_the_generator():
    """The page ships a snapshot; the snapshot must not drift from the code.

    If this fails, regenerate it rather than editing the asset by hand.
    """
    asset = PACK_ROOT / "applet" / "dist" / "assets" / "example.js"
    current = asset.read_text(encoding="utf-8")
    expected = render_example_asset()
    # `generated_at` is a timestamp; compare everything else.
    strip = lambda text: re.sub(r'"generated_at": "[^"]*"', '"generated_at": "<ts>"', text)
    assert strip(current) == strip(expected), (
        "applet/dist/assets/example.js is stale — regenerate it from "
        "evm_dd.examples rather than editing it"
    )
