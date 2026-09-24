"""analytics.deltas: the comparisons, the minimum, the band.

Pure functions only - no store is opened, so nothing here can write outside
its own fixture. The publish path was exercised against a scratch copy of
analytics.db and is reported in a-19, not asserted here.
"""
import random
import re

import pytest

from analytics import deltas


def _sched(team, weeks, season=2026):
    return {team: [(season, w, "g%d_%d" % (season, w)) for w in weeks]}


def _run(schedule, present, value, ok=lambda g, t: True):
    return list(deltas.comparisons(schedule, present, value, ok))


# ---------------------------------------------------------------------------
# the interval
# ---------------------------------------------------------------------------

def test_newcombe_matches_the_published_worked_example():
    # Newcombe (1998), Table II, method 10: 56/70 - 48/80 = 0.2000,
    # 95% interval 0.0524 to 0.3339.
    diff, lo, hi = deltas.newcombe(56, 70, 48, 80, 1.959963984540054)
    assert diff == pytest.approx(0.2)
    assert lo == pytest.approx(0.0524, abs=1e-4)
    assert hi == pytest.approx(0.3339, abs=1e-4)


def test_phi_widens_the_band_and_changes_the_verdict():
    """Discriminating: the same 3-to-10-target swing is a 'change' under a
    pure binomial band and is not under a dispersion of 4."""
    a, b = (10, 30, 1), (3, 30, 1)
    raw = deltas.share_delta(a, b, 1.0)
    wide = deltas.share_delta(a, b, 4.0)
    assert raw.est == wide.est == pytest.approx(7 / 30)
    assert raw.lo > 0                      # excludes zero at phi = 1
    assert wide.lo < 0 < wide.hi           # does not at phi = 4
    assert (wide.hi - wide.lo) > 1.6 * (raw.hi - raw.lo)


def test_parts_carry_games_as_n_and_the_denominator_as_rows():
    parts = deltas.player_parts((6, 30), (3, 25), [(4, 30)] * 4, 1.5)
    assert set(parts) == {"now", "prev", "base", "wow", "vs_base"}
    assert (parts["now"].n, parts["now"].rows) == (1, 30)
    assert (parts["prev"].n, parts["prev"].rows) == (1, 25)
    assert (parts["base"].n, parts["base"].rows) == (4, 120)
    assert (parts["wow"].n, parts["wow"].rows) == (2, 55)
    assert (parts["vs_base"].n, parts["vs_base"].rows) == (5, 150)
    assert parts["wow"].est == pytest.approx(6 / 30 - 3 / 25)
    assert parts["base"].est == pytest.approx(16 / 120)
    for e in parts.values():
        assert e.lo <= e.est <= e.hi


def test_dispersion_is_one_for_binomial_data_and_above_it_when_not():
    rng = random.Random(7)
    binom, over = {}, {}
    for pl in range(300):
        p = rng.uniform(0.05, 0.35)
        binom[(pl, 2025, "T")] = [
            (sum(rng.random() < p for _ in range(35)), 35) for _ in range(12)]
        # game-level role variation on top: the share itself moves each
        # week, drawn ONCE per game (a first version drew it per play, which
        # is just binomial again and measured phi = 1.04)
        games = []
        for _ in range(12):
            q = min(0.95, max(0.0, rng.gauss(p, 0.08)))
            games.append((sum(rng.random() < q for _ in range(35)), 35))
        over[(pl, 2025, "T")] = games
    phi_b = deltas.dispersion(binom, "share")[0]
    phi_o = deltas.dispersion(over, "share")[0]
    assert 0.9 < phi_b < 1.1
    assert phi_o > 1.5


def test_dispersion_refuses_when_nothing_qualifies():
    with pytest.raises(SystemExit):
        deltas.dispersion({("p", 2025, "T"): [(1, 10)] * 3}, "share")


# ---------------------------------------------------------------------------
# the comparisons
# ---------------------------------------------------------------------------

def _value_from(table):
    return lambda g, t, s: table.get((g, s))


def test_a_bye_is_skipped_not_scored_as_zero():
    sched = _sched("A", [1, 2, 3, 4, 5, 7])           # bye in week 6
    present = {(g, "A"): {"p"} for _s, _w, g in sched["A"]}
    vals = {(g, "p"): (w, 30) for _s, w, g in sched["A"]}
    out = _run(sched, present, _value_from(vals))
    wk7 = [o for o in out if o[1][1] == 7]
    assert len(wk7) == 1
    _subj, _g, now, prev, base = wk7[0]
    assert prev == (5, 30)                             # week 5, not a zero
    assert base == [(2, 30), (3, 30), (4, 30), (5, 30)]


def test_a_missed_game_gives_no_week_over_week_but_keeps_the_baseline():
    sched = _sched("A", [1, 2, 3, 4, 5, 6])
    present = {(g, "A"): {"p"} for _s, w, g in sched["A"] if w != 5}
    vals = {(g, "p"): (w, 30) for _s, w, g in sched["A"]}
    out = {o[1][1]: o for o in _run(sched, present, _value_from(vals))}
    assert 5 not in out                                # absent: no value at all
    _subj, _g, now, prev, base = out[6]
    assert prev is None                                # not compared to a zero
    assert base == [(1, 30), (2, 30), (3, 30), (4, 30)]


def test_nothing_is_published_without_four_earlier_appearances():
    sched = _sched("A", [1, 2, 3, 4])
    present = {(g, "A"): {"p"} for _s, _w, g in sched["A"]}
    vals = {(g, "p"): (1, 30) for _s, _w, g in sched["A"]}
    assert _run(sched, present, _value_from(vals)) == []
    sched = _sched("A", [1, 2, 3, 4, 5])
    present = {(g, "A"): {"p"} for _s, _w, g in sched["A"]}
    vals = {(g, "p"): (1, 30) for _s, _w, g in sched["A"]}
    assert [o[1][1] for o in _run(sched, present, _value_from(vals))] == [5]


def test_the_team_snap_minimum_gates_the_week_and_the_previous_week():
    sched = _sched("A", [1, 2, 3, 4, 5, 6, 7])
    present = {(g, "A"): {"p"} for _s, _w, g in sched["A"]}
    vals = {(g, "p"): (w, 30) for _s, w, g in sched["A"]}
    short = {"g2026_6"}
    ok = lambda g, t: g not in short
    out = {o[1][1]: o for o in _run(sched, present, _value_from(vals), ok)}
    assert 6 not in out                                # below the minimum
    assert out[7][3] is None                           # its previous game was
    assert out[5][3] == (4, 30)
    # the short game still counts as an appearance in the baseline
    assert out[7][4][-1] == (6, 30)


def test_week_over_week_never_crosses_a_season_but_the_baseline_may():
    sched = {"A": [(2025, w, "g2025_%d" % w) for w in (15, 16, 17, 18)]
             + [(2026, 1, "g2026_1")]}
    present = {(g, "A"): {"p"} for _s, _w, g in sched["A"]}
    vals = {(g, "p"): (i, 30) for i, (_s, _w, g) in enumerate(sched["A"])}
    out = _run(sched, present, _value_from(vals))
    assert len(out) == 1
    _subj, (season, week, _g), now, prev, base = out[0]
    assert (season, week) == (2026, 1)
    assert prev is None
    assert len(base) == 4


def test_a_player_who_changes_team_starts_again():
    sched = {"A": [(2026, w, "a%d" % w) for w in (1, 2, 3, 4)],
             "B": [(2026, w, "b%d" % w) for w in (5, 6, 7, 8, 9)]}
    present = {}
    for t, games in sched.items():
        for _s, _w, g in games:
            present[(g, t)] = {"p"}
    vals = {(g, "p"): (1, 30) for games in sched.values()
            for _s, _w, g in games}
    out = _run(sched, present, _value_from(vals))
    assert [(o[1][1]) for o in out] == [9]             # 4 games with B first


def test_an_undefined_share_is_no_value_not_zero():
    """A team with no red-zone snaps: the share has no denominator."""
    sched = _sched("A", [1, 2, 3, 4, 5, 6])
    present = {(g, "A"): {"p"} for _s, _w, g in sched["A"]}
    vals = {(g, "p"): (1, 5) for _s, w, g in sched["A"] if w != 5}
    out = {o[1][1]: o for o in _run(sched, present, _value_from(vals))}
    assert 5 not in out
    assert out[6][3] is None


# ---------------------------------------------------------------------------
# the registry
# ---------------------------------------------------------------------------

KEY = re.compile(r"^analytics/[a-z0-9]+/[a-z0-9_]+(\.[a-z0-9_]+)+\.json$")


@pytest.mark.parametrize("measure",
                         deltas.PLAYER_MEASURES + deltas.TEAM_MEASURES)
def test_every_metric_declares_itself_and_matches_the_contract_key(measure):
    m = deltas.metric_for(measure, 2026, "w02", note="x")
    assert KEY.match("analytics/nfl/%s.json" % m.key)
    assert m.key == "deltas.%s.w02" % measure
    assert m.availability == "current"
    assert m.floor_season == 2026
    if measure in deltas.PLAYER_MEASURES:
        assert m.shares_denominator == "team"
        assert m.subject_type == "player"
    else:
        assert m.subject_type == "team"


def test_the_minimum_is_the_pre_registered_one():
    assert deltas.MIN_TEAM_SNAPS == 40
    assert deltas.BASELINE_APPEARANCES == 4


# ---------------------------------------------------------------------------
# compute, over a hand-built Data
# ---------------------------------------------------------------------------

def _fake_data():
    d = deltas.Data.__new__(deltas.Data)
    games = [(2025, w, "g25_%d" % w) for w in range(1, 11)] + \
            [(2026, w, "g26_%d" % w) for w in (1, 2)]
    d.schedule = {"A": games}
    d.present, d.usage, d.team_usage = {}, {}, {}
    d.position = {"wr": "WR", "qb": "QB"}
    d.team_snaps = {}
    rng = random.Random(3)
    for _s, _w, g in games:
        d.present[(g, "A")] = {"wr", "qb"}
        d.team_snaps[(g, "A")] = 65
        t = rng.randint(3, 12)
        d.usage[(g, "wr")] = [t, 0]
        d.usage[(g, "qb")] = [0, 2]
        d.team_usage[(g, "A")] = [34, 25]
    return d


def test_compute_publishes_a_player_with_a_job_and_not_one_without():
    d = _fake_data()
    rows, disp = deltas.compute(d, "target_share", {2026}, {2025})
    subjects = {s for s, _sl, _e in rows}
    assert subjects == {"wr"}                  # the QB has no target share
    slices = {sl for _s, sl, _e in rows}
    assert "w01|vs_base" in slices and "w01|wow" not in slices
    assert {"w02|now", "w02|prev", "w02|base", "w02|wow",
            "w02|vs_base"} <= slices
    assert disp[None][0] > 0


def test_compute_publishes_nothing_for_a_season_it_was_not_asked_for():
    d = _fake_data()
    rows, _disp = deltas.compute(d, "target_share", {2024}, {2025})
    assert rows == []
