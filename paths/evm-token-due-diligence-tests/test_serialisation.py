"""Exactness survives the trip to JSON.

A supply of 10**24 is a normal number for a token and an unrepresentable
one for a JSON consumer using 64-bit floats. Every base-unit amount leaves
this pack as a decimal string.
"""

from __future__ import annotations

import json

from evm_dd.examples import EXAMPLES, build
from evm_dd.reconcile import Direction, Flow, Reconciliation
from evm_dd.target import JSON_SAFE_INTEGER

UNSAFE = JSON_SAFE_INTEGER


def _walk(node, path="$"):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk(value, f"{path}[{index}]")
    else:
        yield path, node


def test_no_example_report_carries_an_unsafe_integer_or_a_float():
    for name in EXAMPLES:
        document = json.loads(json.dumps(build(name)))
        for path, value in _walk(document):
            assert not isinstance(value, float), f"{name}: float at {path}"
            if isinstance(value, int) and not isinstance(value, bool):
                assert abs(value) <= UNSAFE, f"{name}: unsafe integer at {path}"


def test_a_large_supply_survives_as_an_exact_string():
    document = build("locked-canonical-removable-side-pool")
    supply = document["target"]["metadata"]["total_supply"]
    assert supply["state"] == "resolved"
    assert supply["value"] == "1000000000000000000000000"
    assert int(supply["value"]) == 10**24


def test_reconciliation_amounts_serialise_as_exact_strings():
    window = Reconciliation(
        asset="WETH",
        holder="0x" + "aa" * 20,
        decimals=18,
        opening=0,
        closing=10**24,
        from_block=1,
        to_block=2,
    )
    window.add(Flow("inbound", 10**24, Direction.IN))
    payload = json.loads(json.dumps(window.to_dict()))
    assert payload["closing"] == "1000000000000000000000000"
    assert payload["flows"][0]["amount"] == "1000000000000000000000000"
    assert int(payload["closing"]) - int(payload["inflows"]) == 0
