"""The contract's key table, and what it forces a sport's URLs to look like.

Run: pytest -q tests/test_contract_keys.py

`x-contract.keys` is not documentation: `jobs/export_web.KIND_BY_KEY` is built
from it and `validate_contract` picks a file's validator by walking it in order,
first match wins. So a pattern here decides both which shape a file is checked
against and, for teams, what a URL can spell.

C-1 (track C, filed 2026-09-18): the team pattern was `[a-z0-9]+`, which has no
hyphen, so `cfb/teams/alabama-crimson-tide.json` failed validation while
`cfb/teams/ala.json` passed. **The NFL cannot see this defect at all** because
its slugs ARE abbreviations - which is exactly why a second sport was the test.
The contract was deciding that a multi-word school gets an abbreviation-shaped
URL, and that is a product decision the contract has no business making.

Widening is safe in the direction that matters: it admits keys that were refused
and cannot invalidate a key that already passed. The tests below pin that rather
than assume it.
"""
import re

from jobs import export_web as E

KEYS = E.CONTRACT["x-contract"]["keys"]


def resolve(key):
    """Every kind whose pattern matches - not just the first, so an ambiguity
    is visible rather than hidden by precedence."""
    return [k["kind"] for k in KEYS if re.match(k["pattern"], key)]


# ----------------------------------------------------------- no key is ambiguous

def test_no_key_shape_resolves_to_two_kinds():
    """First match wins, so an overlap silently validates a file against the
    wrong shape. That is worse than refusing it."""
    samples = [
        "sports.json",
        "nfl/manifest.json", "cfb/manifest.json",
        "nfl/players/index.json", "cfb/players/index.json",
        "nfl/players/00-0036223/summary.json",
        "nfl/players/00-0036223/2025.json",
        "nfl/teams/buf.json", "cfb/teams/ala.json",
        "cfb/teams/alabama-crimson-tide.json",
        "nfl/market/00-0036223/2026-2.json",
        "research/hypotheses.json", "research/calibration.json", "research/execution.json",
        "analytics/nfl/index.json", "analytics/nfl/role.touch_share.json",
    ]
    for key in samples:
        hits = resolve(key)
        assert len(hits) <= 1, f"{key} matches {hits}"


def test_the_sample_set_actually_resolves():
    """Exit 0 is not a result: a sample list that matched nothing would pass the
    test above while checking nothing."""
    assert resolve("nfl/teams/buf.json") == ["team"]
    assert resolve("analytics/nfl/index.json") == ["analytics.index"]
    assert resolve("nfl/players/00-0036223/2025.json") == ["player_season"]


# ------------------------------------------------------------------- C-1 itself

def test_a_hyphenated_team_slug_is_accepted():
    """The finding. A sport whose teams are multi-word schools needs a readable
    team URL, and the contract used to forbid one."""
    assert resolve("cfb/teams/alabama-crimson-tide.json") == ["team"]


def test_abbreviation_shaped_team_slugs_STILL_resolve():
    """Widening must not disturb what already worked - the NFL's whole team
    space is abbreviations, and those URLs are published."""
    for slug in ("buf", "la", "lac", "lv", "sd", "oak", "stl"):
        assert resolve(f"nfl/teams/{slug}.json") == ["team"], slug


def test_a_team_key_may_not_START_with_a_hyphen():
    """The other answer on the other input. A pattern that accepted everything
    would satisfy the two tests above."""
    assert resolve("cfb/teams/-leading-hyphen.json") == []


def test_a_team_key_still_refuses_the_shapes_it_always_did():
    assert resolve("cfb/teams/Alabama.json") == []          # upper case
    assert resolve("cfb/teams/alabama_crimson.json") == []  # underscore
    assert resolve("cfb/teams/a/b.json") == []              # a path, not a slug
    assert resolve("cfb/teams/alabama.txt") == []           # not json


def test_widening_the_team_pattern_reaches_no_other_kind():
    """A hyphenated TEAM key must not become matchable as some other kind, and
    no other kind's keys may start resolving as teams."""
    assert resolve("analytics/nfl/index.json") == ["analytics.index"]
    assert resolve("nfl/market/00-0036223/2026-2.json") == ["market"]
    assert resolve("nfl/players/index.json") == ["player_index"]


def test_the_producer_agrees_with_this_table():
    """`KIND_BY_KEY` is derived from the same list - if it ever stops being, this
    file is testing a document nothing reads."""
    assert len(E.KIND_BY_KEY) == len(KEYS)
    assert E.kind_for_key("cfb/teams/alabama-crimson-tide.json")[0] == "team"
    assert E.kind_for_key("nfl/teams/buf.json")[0] == "team"
    assert E.kind_for_key("cfb/teams/-leading-hyphen.json")[0] is None
