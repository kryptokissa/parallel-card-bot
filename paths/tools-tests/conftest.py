"""Tests for the shared build-time tools in paths/tools/.

Named `tools-tests` to match the `<slug>-tests` convention: nothing here is
inside a path directory, so none of it can end up in a published bundle.
"""

from __future__ import annotations

import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))
