"""Tests live beside the path, not inside it.

Everything under the path directory is packaged into the published
bundle — `wayfinder path build` has no exclude option — so a tests/
folder in there ships to every install and is scanned as though it were
runtime code. It is not runtime code, and the people installing the path
should not have to download it.

One level up, the bundle carries only what runs, and the suite still
imports the pack directly from source.

`tests/evals/` stays inside the path: those are two small YAML eval
specs that `wayfinder path eval` looks for at a fixed location, not test
code.
"""

import os
import sys

PATH_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "evm-token-due-diligence",
)
if PATH_DIR not in sys.path:
    sys.path.insert(0, PATH_DIR)
