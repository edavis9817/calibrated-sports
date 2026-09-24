"""The Strategy Lab engine (AUDIT-2026-09-24 section 6; unit a-27).

    from lab import run
    result = run(strategy, universe)          # pure: no I/O, seeded by the rule

`lab.universe` builds the table from the store (read-only); `lab.presets`
holds the library rules that must reproduce the register.
"""
from lab.engine import run  # noqa: F401
from lab.strategy import RESULT_ID, SCHEMA_ID  # noqa: F401
