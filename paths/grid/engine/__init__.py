"""Grid engine: decides levels and orders. It never signs or sends anything.

The path decides, the agent executes (BUILDING_PATHS.md §2.1). Every module
here is pure computation over a frozen config plus observed exchange state.
"""

__all__ = [
    "config",
    "ticks",
    "levels",
    "gates",
    "interview",
    "plan",
    "reconcile",
]
