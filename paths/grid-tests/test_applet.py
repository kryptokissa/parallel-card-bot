"""The applet, which ships and which review reads.

Grid 0.1.0 was sent to human review because the `path init` applet scaffold
posts to a wildcard target origin. The scaffold is not publishable as it stands,
and since it arrives that way in every new path, the fix needs a test rather
than a memory.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


# Resolved from this file, not from pytest's rootdir: PUBLISHING.md §1 runs the
# suite from the parent directory, and anything keyed to the invocation
# directory breaks under the documented workflow.
APPLET_DIR = Path(__file__).resolve().parent.parent / "grid" / "applet" / "dist"


@pytest.fixture(scope="module")
def applet_js():
    return (APPLET_DIR / "assets" / "app.js").read_text()


@pytest.fixture(scope="module")
def applet_html():
    return (APPLET_DIR / "index.html").read_text()


def test_no_postmessage_carries_a_wildcard_target(applet_js):
    assert not re.search(r"""postMessage\s*\([^)]*?['"]\*['"]""", applet_js)


def test_the_only_postmessage_targets_the_captured_origin(applet_js):
    calls = re.findall(r"postMessage\s*\(([^;]*?)\)\s*;", applet_js)
    assert calls, "expected at least one postMessage call"
    for call in calls:
        assert "parentOrigin" in call, call


def test_the_parent_origin_is_captured_from_the_event(applet_js):
    assert "event.origin" in applet_js
    assert "parentOrigin = event.origin" in applet_js


def test_the_captured_origin_is_validated(applet_js):
    assert "isValidOrigin" in applet_js
    assert re.search(r"/\^https\?:", applet_js), "expected a scheme check"


def test_messages_from_another_window_are_rejected(applet_js):
    # Origin alone is not enough: the sender must be the parent frame.
    assert applet_js.count("event.source !== window.parent") >= 2


def test_nothing_is_sent_before_the_handshake(applet_js):
    assert "if (!parentOrigin) return;" in applet_js


def test_later_messages_must_match_the_captured_origin(applet_js):
    assert "event.origin !== parentOrigin" in applet_js


def test_the_applet_has_no_inline_event_handlers(applet_html):
    assert not re.search(r"\bon(?:click|load|error|mouseover)\s*=", applet_html)


def test_the_applet_ships_no_scaffold_placeholders(applet_html):
    # The scaffold's "Bump state" counter is not a view of anything.
    assert "data-action" not in applet_html
    assert "Bump state" not in applet_html


def test_the_applet_says_it_is_read_only(applet_html):
    assert "Read-only" in applet_html


def test_the_applet_warns_when_breakout_is_unanswered(applet_html):
    # The one decision with no safe default should be visible as missing.
    assert "data-breakout-warning" in applet_html
    assert "unhedged" in applet_html


def test_the_applet_renders_the_things_that_matter(applet_html):
    for attribute in (
        "data-market",
        "data-range",
        "data-levels",
        "data-spacing",
        "data-leverage",
        "data-breakout",
        "data-rungs",
        "data-gates",
    ):
        assert attribute in applet_html, attribute
