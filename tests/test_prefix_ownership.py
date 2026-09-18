"""No producer-owned prefix may reach track F's `analytics/`.

Run: pytest -q tests/test_prefix_ownership.py

`sync_keys(dest, wanted, prefixes)` DELETES every local key under `prefixes`
that the builder did not produce. Its contract is "this builder owns this
prefix", and the cost of getting it wrong is measured: twelve market keys were
deleted in one night by a builder firing over a prefix holding data it does not
make.

WHY THIS IS A GUARD AND NOT A `sync_keys` CALL. Track F asked track A to own
`analytics/` so nothing else deletes it. The inverse is what actually protects
them. A producer-side call owning that prefix would hand `sync_keys` an EMPTY
wanted set on every ordinary run - track A has no analytics builder, F's export
lives in its own root behind its own database - and an empty wanted set over an
owned prefix is a delete-everything instruction. Not a mistake someone might
make: what the design does on a Tuesday. It would also put two builders on one
prefix, which is the defect the rule exists to prevent.

So track A owns nothing under `analytics/`, and this asserts that categorically:
no producer prefix may CONTAIN or BE CONTAINED BY it. It pairs with track F's
own AST check over their call sites - each track guards the boundary from its
own side, and neither depends on the other remembering.

BY AST, NOT BY GREP. A docstring quoting a prefix reads identically to a live
call in a text search, and this file's own module docstring says `analytics/`
several times. The same reason `tests/test_settlement.py` matches call sites on
the AST.
"""
import ast
import os

import pytest

from jobs import export_web as E

FOREIGN = "analytics/"
SOURCE = os.path.join(E.ROOT, "jobs", "export_web.py")


def _literal(node, ns):
    """A prefix string from a Constant or an f-string, or None if not static."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        out = []
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                out.append(part.value)
            elif isinstance(part, ast.FormattedValue) and isinstance(part.value, ast.Name):
                if part.value.id not in ns:
                    return None          # a name this test cannot resolve
                out.append(str(ns[part.value.id]))
            else:
                return None
        return "".join(out)
    return None


def owned_prefixes(source_path=SOURCE, ns=None):
    """Every prefix literal handed to `sync_keys`, and whether any was dynamic.

    -> (prefixes, dynamic_calls). `dynamic_calls` is the honest half: a prefix
    this test cannot evaluate is one it cannot vouch for, and reporting zero of
    those is what makes the assertion below mean something.
    """
    ns = ns or {"SPORT": E.SPORT}
    with open(source_path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    prefixes, dynamic = [], 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name != "sync_keys":
            continue
        if len(node.args) < 3:
            dynamic += 1                  # prefixes passed by keyword or splat
            continue
        arg = node.args[2]
        if not isinstance(arg, (ast.List, ast.Tuple)):
            dynamic += 1
            continue
        for element in arg.elts:
            value = _literal(element, ns)
            if value is None:
                dynamic += 1
            else:
                prefixes.append(value)
    return prefixes, dynamic


def containment_violations(prefixes, foreign=FOREIGN):
    """[(prefix, reason)] for any prefix that reaches `foreign`, either way.

    An EMPTY prefix is a violation on its own: `"".startswith` is true of every
    key, so it owns the entire tree including another track's.
    """
    out = []
    for p in prefixes:
        if p == "":
            out.append((p, "an empty prefix owns every key in the tree"))
        elif p.startswith(foreign):
            out.append((p, f"is inside {foreign}, which track F owns"))
        elif foreign.startswith(p):
            out.append((p, f"contains {foreign}, so its builder would delete F's keys"))
    return out


# --------------------------------------------------------------- the guard

def test_no_producer_prefix_reaches_analytics():
    prefixes, _ = owned_prefixes()
    bad = containment_violations(prefixes)
    assert not bad, "prefixes reaching track F's keys: " + "; ".join(
        f"{p!r} {why}" for p, why in bad)


def test_the_scan_actually_found_the_call_sites():
    """Exit 0 is not a result. A walk that matched nothing would pass the test
    above while checking nothing at all."""
    prefixes, dynamic = owned_prefixes()
    assert len(prefixes) >= 3, f"only found {prefixes} - did the AST walk break?"
    assert any(p.endswith("/players/") for p in prefixes), prefixes
    assert any(p.endswith("/teams/") for p in prefixes), prefixes
    assert "research/" in prefixes, prefixes


def test_every_prefix_was_statically_resolvable():
    """A prefix this test cannot evaluate is one it cannot vouch for. If a call
    site starts building its prefix at runtime, this fails and says so rather
    than quietly checking the subset it understood."""
    _, dynamic = owned_prefixes()
    assert dynamic == 0, f"{dynamic} sync_keys call(s) pass a prefix this guard cannot evaluate"


# ------------------------------------------------------------ it discriminates

@pytest.mark.parametrize("prefix", ["analytics/", "analytics/nfl/", "analytics/nfl/x.json", ""])
def test_a_prefix_that_reaches_analytics_is_caught(prefix):
    """Driven into every banned shape: owning it exactly, owning a part of it,
    and the empty prefix that owns everything."""
    assert containment_violations([prefix]), f"{prefix!r} was not caught"


@pytest.mark.parametrize("prefix", ["nfl/players/", "nfl/teams/", "research/", "nfl/market/"])
def test_a_legitimate_prefix_is_not_caught(prefix):
    """The other answer, on the other input. Without this a checker that always
    reported a violation would satisfy the test above."""
    assert containment_violations([prefix]) == []


def test_containment_is_checked_in_BOTH_directions():
    """`nfl/` does not reach `analytics/`, but a bare `` or `a` prefix would.
    The rule is mutual containment, not 'starts with analytics'."""
    assert containment_violations(["analytics/nfl/"])      # inside it
    assert containment_violations([""])                    # contains it
    assert containment_violations(["a"])                   # contains it
    assert containment_violations(["nfl/"]) == []          # unrelated
