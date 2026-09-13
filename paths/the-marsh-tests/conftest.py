"""Tests live beside the path, not inside it.

Everything under the path directory is packaged into the published
bundle -- there is no exclude option in `wayfinder path build` -- so a
tests/ folder in there ships to every install and is scanned as though
it were runtime code. It also isn't runtime code, and shouldn't be
downloaded by people installing a game.

Keeping them one level up means the bundle carries only what runs, and
the suite still imports the pack directly from source.
"""

import os
import sys

PATH_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "the-marsh")
if PATH_DIR not in sys.path:
    sys.path.insert(0, PATH_DIR)
