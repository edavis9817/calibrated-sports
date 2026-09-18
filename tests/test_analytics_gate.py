"""Track F's structural rule, enforced.

    No analytic is published without an interval and a sample count.

A guard that has never been seen to fail is not a guard, so most of this file
drives `analytics.gate` into each banned shape and asserts it refuses. The
positive cases matter too: a gate that fails everything would also pass this
file's negative cases and would block the package instead of protecting it.
"""
import json

import pytest

from analytics import gate
from analytics.intervals import Estimate, wilson, student_t, block_bootstrap


# =============================================================================
# the gate refuses an estimate without its apparatus
# =============================================================================

BANNED = [
    # (table, columns, the word the reason must contain)
    ("f_usage", ["player_id", "target_share"], "interval"),
    ("f_usage", ["player_id", "target_share", "target_share_lo",
                 "target_share_hi"], "sample count"),
    ("f_usage", ["player_id", "target_share", "n"], "interval"),
    ("f_pace", ["team", "plays_per_game"], "interval"),
    ("f_pace", ["team", "neutral_pace_mean", "n"], "interval"),
    ("f_role", ["player_id", "third_short_snap_rate", "n"], "interval"),
    ("f_air", ["player_id", "adot_mean", "adot_mean_lo", "adot_mean_hi"],
     "sample count"),
    ("f_air", ["player_id", "air_yards_p90", "n"], "interval"),
    ("f_stability", ["metric", "autocorr_lag1"], "interval"),
    ("f_stability", ["metric", "week_to_week_correlation", "n"], "interval"),
    ("f_script", ["player_id", "trailing_route_rate", "trailing_route_rate_lo",
                  "trailing_route_rate_hi"], "sample count"),
    ("f_script", ["player_id", "usage_elasticity", "n"], "interval"),
]


@pytest.mark.parametrize("table,cols,word", BANNED)
def test_the_gate_catches_each_banned_shape(table, cols, word):
    v = gate.column_violations(table, cols)
    assert v, f"{table}{cols} passed the gate and must not have"
    assert any(word in reason for _t, _c, reason in v), [r for *_x, r in v]


PERMITTED = [
    ("f_usage", ["player_id", "season", "target_share", "target_share_lo",
                 "target_share_hi", "n"]),
    ("f_usage", ["player_id", "target_share", "target_share_lo",
                 "target_share_hi", "target_share_n",
                 "carry_share", "carry_share_lo", "carry_share_hi",
                 "carry_share_n"]),
    # raw counts and identifiers are not estimates and need nothing
    ("f_plays", ["game_id", "player_id", "plays", "targets", "carries",
                 "routes", "snaps", "week", "season", "yards_gained"]),
    # a posted threshold is not an estimate
    ("f_lines", ["game_id", "player_id", "market", "line", "actual"]),
    # play-level nflverse columns carried verbatim
    ("f_pbp", ["play_id", "game_id", "epa", "wpa", "cpoe", "success", "xpass"]),
]


@pytest.mark.parametrize("table,cols", PERMITTED)
def test_the_gate_permits_a_complete_analytic(table, cols):
    assert gate.column_violations(table, cols) == []


def test_a_mixed_sample_table_must_name_n_per_estimate():
    """Two estimates over different samples may not share one bare `n`... is
    NOT what the gate can see. What it can see is that each estimate has A
    sample count, and `{col}_n` is how a mixed table says which."""
    ok = ["adot_mean", "adot_mean_lo", "adot_mean_hi", "adot_mean_n",
          "deep_rate", "deep_rate_lo", "deep_rate_hi", "deep_rate_n"]
    assert gate.column_violations("f_air", ok) == []


# =============================================================================
# the same rule over an export payload
# =============================================================================

def test_an_export_payload_with_a_bare_rate_is_refused():
    payload = {"sport": "nfl", "players": [
        {"id": "00-0036355", "target_share": 0.24, "target_share_lo": 0.19,
         "target_share_hi": 0.30, "n": 17},
        {"id": "00-0033873", "target_share": 0.19},
    ]}
    v = gate.payload_violations(payload)
    assert len(v) >= 1
    assert all(p.startswith("$.players[1]") for p, _c, _r in v)


def test_a_child_object_may_not_inherit_its_parents_n():
    """The failure this prevents: a figure quoting a sample it was not computed
    on because a wrapper object happened to carry one."""
    payload = {"n": 900, "deep_rate": 0.31, "deep_rate_lo": 0.2, "deep_rate_hi": 0.4,
               "by_down": {"third": {"deep_rate": 0.44, "deep_rate_lo": 0.3,
                                     "deep_rate_hi": 0.6}}}
    v = gate.payload_violations(payload)
    assert [c for _p, c, _r in v] == ["deep_rate"]
    assert "sample count" in v[0][2]


def test_a_complete_payload_passes_and_round_trips_as_json():
    e = wilson(45, 200)
    payload = {"player_id": "00-0036355", **e.flat("third_short_rate")}
    assert gate.payload_violations(payload) == []
    assert json.loads(json.dumps(payload))["third_short_rate_n"] == 200


# =============================================================================
# the estimate type: unconstructible without its sample
# =============================================================================

def test_an_estimate_cannot_be_built_without_a_sample_count():
    with pytest.raises(ValueError, match="sample count"):
        Estimate(0.5, 0.4, 0.6, None, "made up")


def test_an_estimate_whose_interval_excludes_its_point_is_refused():
    with pytest.raises(ValueError, match="excludes"):
        Estimate(0.5, 0.6, 0.7, 10, "wrong")


def test_wilson_not_the_normal_approximation_at_a_low_rate():
    """CLAUDE.md, brief 022: the normal interval put a priced value outside a
    bucket it was inside. 15/167 at 0.0898 must keep 0.1388 inside."""
    e = wilson(15, 167)
    assert e.lo < 0.1388 < e.hi
    assert e.lo > 0                     # a normal interval goes negative here


def test_an_interval_on_fewer_than_five_blocks_is_not_readable():
    e = block_bootstrap({g: [1.0, 1.0] for g in range(3)},
                        lambda rs: sum(rs) / len(rs))
    assert e.excludes_zero and not e.readable


# =============================================================================
# the block bootstrap must not narrow on duplicated rows
# =============================================================================

def test_duplicating_rows_inside_their_own_game_does_not_narrow_the_interval():
    """`tests/test_clv.py` guards the same property for the CLV harness. A
    normal-approximation interval on the duplicated rows would be ~sqrt(20)
    too narrow; the block bootstrap must be indifferent to it."""
    import random
    rng = random.Random(7)
    base = {g: [rng.gauss(0.2, 1.0) for _ in range(6)] for g in range(14)}
    blown = {g: rows * 20 for g, rows in base.items()}
    mean = lambda rs: sum(rs) / len(rs)
    a = block_bootstrap(base, mean)
    b = block_bootstrap(blown, mean)
    assert a.n == b.n == 14
    assert a.rows == 84 and b.rows == 1680
    wide_a, wide_b = a.hi - a.lo, b.hi - b.lo
    assert wide_b > wide_a * 0.9, (wide_a, wide_b)


def test_the_sample_count_is_blocks_not_rows():
    e = block_bootstrap({g: [1.0] * 50 for g in range(9)},
                        lambda rs: sum(rs) / len(rs))
    assert e.n == 9 and e.rows == 450


def test_a_mean_over_units_reports_its_unit_count():
    e = student_t([3.0, 4.0, 5.0, 6.0])
    assert e.n == 4 and e.lo < e.est < e.hi


# =============================================================================
# the draws must be independent between subjects (Ethan, 2026-09-17)
# =============================================================================

def test_two_subjects_do_not_share_bootstrap_draws():
    """CRN is anti-conservative for negatively correlated subjects, and two
    receivers on one team share a denominator. `analytics/crn_check.py` has the
    measurement; this is the guard that the fix stays in.

    Two subjects with IDENTICAL data must still get different draws - under the
    old cache they got byte-identical intervals, which is the visible symptom
    of the coupling."""
    from analytics.intervals import share_bootstrap
    blocks = {g: (3, 10 + g) for g in range(14)}
    a = share_bootstrap(blocks, subject="00-0000001")["share"]
    b = share_bootstrap(blocks, subject="00-0000002")["share"]
    assert a.est == b.est, "the point estimate is not a random quantity"
    assert (a.lo, a.hi) != (b.lo, b.hi), (
        "two subjects share a resampling matrix; see analytics/crn_check.py")


def test_one_subject_reproduces_exactly_across_runs():
    """Independent between subjects must not mean irreproducible. The seed is
    a stable hash of the subject - NOT the builtin `hash()`, which is salted
    per process and would move every published figure on every run."""
    from analytics.intervals import share_bootstrap
    blocks = {g: (3, 10 + g) for g in range(14)}
    a = share_bootstrap(blocks, subject="00-0036355")["share"]
    b = share_bootstrap(blocks, subject="00-0036355")["share"]
    assert (a.lo, a.hi) == (b.lo, b.hi)


def test_the_resampling_matrix_is_not_memoised_across_subjects():
    """A seam guard of the same shape as the `run_scan` one: assert on the
    source, because the behaviour above could be satisfied by a cache keyed on
    the subject while leaving the old cache in place for another caller."""
    import inspect

    from analytics import intervals
    src = inspect.getsource(intervals)
    assert "_COUNTS_CACHE" not in src
    assert "subject" in inspect.signature(intervals._counts_matrix).parameters


# =============================================================================
# shared denominators (track F standing rule, Ethan 2026-09-17)
# =============================================================================

def _metric(**kw):
    from analytics.metrics import Metric
    base = dict(key="x.y", label="L", unit="u", subject_type="player",
                block="game", basis="pbp", availability="current")
    base.update(kw)
    return Metric(**base)


@pytest.mark.parametrize("key,unit", [
    ("usage.target_share", "share of team targets"),
    ("usage.snap_pct", "snaps as a percent of team plays"),
    ("x.route_share", "share of team routes"),
    ("x.redzone_proportion", "proportion of team red-zone looks"),
])
def test_a_share_shaped_metric_must_say_what_two_subjects_divide(key, unit):
    """The rule is about the QUANTITY, not about the five metrics that exist.
    Any future share-of-team figure has the same property: two teammates
    divide one total, so their shares are mechanically opposed."""
    with pytest.raises(ValueError, match="two subjects divide"):
        _metric(key=key, unit=unit)


def test_own_is_an_answer_and_silence_is_not():
    """An air-yard bin share divides the player's OWN targets, so two players
    share nothing. That has to be stated, not left to a default - otherwise
    every false positive is silenced by the same mechanism as the real ones."""
    m = _metric(key="air_yards.bins", unit="share of targets in each band",
                shares_denominator="own")
    assert m.shares_denominator == "own"


def test_a_metric_that_is_not_a_share_needs_no_denominator():
    m = _metric(key="pace.seconds_per_play", unit="seconds between snaps")
    assert m.shares_denominator is None


def test_an_unknown_denominator_is_refused():
    with pytest.raises(ValueError, match="names what two subjects divide"):
        _metric(key="x.share", unit="share of team", shares_denominator="teem")


def test_every_published_share_metric_declares_team_or_own():
    """Drives the real metric constructors, so a new analytic that forgets
    fails here rather than at publish time on the dev box."""
    from analytics import airyards, pace, role, script, stability
    built = ([script.metric_for(k) for k in script.KINDS]
             + [role.metric_for(k) for k in role.KINDS]
             + [pace.metric_for(k, p) for k in pace.KINDS for p in (False, True)]
             + [airyards.metric_for(r, f) for r in airyards.ROLES
                for f in ("bins", "quantiles", "polarity")]
             + [stability.metric_for(f, k) for f in stability.FAMILIES
                for k in stability.KINDS])
    assert len(built) > 25
    for m in built:
        if m.shares_denominator == "team":
            assert "share" in m.key or "share" in m.unit.lower()
    teams = {m.key for m in built if m.shares_denominator == "team"}
    assert "role.touch_share" in teams and "role.onfield_share" in teams
    assert "air_yards.bins.receiver" not in teams
