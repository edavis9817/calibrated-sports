"""A period row carries the keys the player's production justifies.

Run: pytest -q tests/test_period_keys.py

WHY. Folding every stat the sport records into one fixed key list means a
receiver's page carries eleven defensive zeros and a linebacker's carries the
receiving block. `stats = {k: 0 for k in PERIOD_KEYS}` MANUFACTURES those zeros -
they are not in the source at all - which is the silent-zero defect self-inflicted,
the day after a day spent removing the last one. The populations are mostly
disjoint: 759 players are offensive-only, 6,856 defensive-only, 3,233 both.

THE RULE, and the null clause is the load-bearing half:

    emit a key if ANY period has a NON-ZERO value **or** a NULL for it;
    omit it only if it is zero in EVERY period.

Null must keep its key. `null` means "unknown or not collected" - 2003-08 targets,
2003-2011 def_tfl - and that absence is itself the information the page shows as
"not recorded". Dropping the key would turn a documented gap back into silence,
undoing the silent-zero work rather than extending it.

USAGE KEYS ARE EXEMPT. A played-zero row is DEFINED by snaps > 0 with every stat
zero; applying the rule to `snaps` would delete the one key proving he played and
leave a row asserting nothing. So the rule ranges over stat keys only.

ORDER IS PRESERVED from the candidate list, so a re-export of unchanged data
produces byte-identical files and `write_if_changed` stays quiet.
"""
import pytest

from jobs import export_web as E


def s(**kw):
    """One period's stats dict."""
    return dict(kw)


# --------------------------------------------------------------- the rule

def test_a_key_that_is_zero_in_every_period_is_dropped():
    keys = E.emitted_keys([s(rec=0, pass_att=0), s(rec=3, pass_att=0)], ("rec", "pass_att"))
    assert "pass_att" not in keys
    assert "rec" in keys


def test_a_key_non_zero_in_ANY_period_is_kept():
    """One catch in one game is a fact about the player."""
    keys = E.emitted_keys([s(rec=0), s(rec=0), s(rec=1)], ("rec",))
    assert "rec" in keys


def test_a_key_that_is_NULL_anywhere_is_kept():
    """THE CLAUSE THAT PRESERVES THE SILENT-ZERO WORK. null means 'not recorded'
    - 2003-08 targets, 2003-2011 def_tfl - and the page renders that absence.
    Dropping the key would turn a documented gap back into silence."""
    keys = E.emitted_keys([s(targets=None), s(targets=0)], ("targets",))
    assert "targets" in keys


def test_null_everywhere_is_still_kept():
    keys = E.emitted_keys([s(targets=None), s(targets=None)], ("targets",))
    assert "targets" in keys, "a wholly uncollected stat must still say so"


def test_a_missing_key_counts_as_zero():
    """A period row that never carried the key is not evidence of production."""
    keys = E.emitted_keys([s(rec=0), s()], ("rec", "rush_att"))
    assert keys == ()


def test_negative_values_are_production():
    """Rushing yards can be negative. `if value` would drop -7 as falsy; the rule
    is 'not zero', not 'truthy'."""
    keys = E.emitted_keys([s(rush_yds=0), s(rush_yds=-7)], ("rush_yds",))
    assert "rush_yds" in keys


def test_order_follows_the_candidate_list():
    """A re-export of unchanged data must be byte-identical or
    `write_if_changed` rewrites every file on every run."""
    cands = ("rec", "rec_yds", "rush_att")
    keys = E.emitted_keys([s(rush_att=1, rec=1, rec_yds=1)], cands)
    assert keys == cands


def test_an_empty_period_list_emits_nothing():
    assert E.emitted_keys([], ("rec",)) == ()


# ------------------------------------------------------- what it ranges over

def test_usage_keys_are_not_candidates():
    """A played-zero row is snaps > 0 with every stat zero. If the rule ranged
    over `snaps` it would delete the key that proves he played, and the row
    would assert nothing at all."""
    assert "snaps" not in E.STAT_CANDIDATES
    assert "snap_share" not in E.STAT_CANDIDATES


def test_every_candidate_is_a_declared_stat():
    """`assert_stats_defined` refuses any emitted key absent from the manifest,
    so a candidate with no definition would fail the export rather than the
    test."""
    for k in E.STAT_CANDIDATES:
        assert k in E.STAT_DEFINITIONS, k


def test_the_candidates_cover_the_offensive_map():
    """Nothing silently stops being emittable."""
    for _col, key in E.STAT_MAP:
        assert key in E.STAT_CANDIDATES, key
