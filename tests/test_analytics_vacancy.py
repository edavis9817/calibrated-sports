"""analytics.vacancy - the event rules, driven by a hand-built team-season.

Pure: no store is opened. Every rule is shown firing AND not firing, because a
rule that is only ever seen to pass could be asserting nothing.
"""
import pytest

from analytics import vacancy

SEASON = 2020


def _season(weeks, team="AAA"):
    """weeks: {week: {pfr: (group, snap_pct, targets, carries)}}.

    Returns the four inputs find_events takes. Team targets and carries are
    the sums over the listed players, so shares are exact.
    """
    apps, totals, present, games = [], {}, set(), set()
    for week, players in weeks.items():
        game = "%d_%02d_%s_ZZZ" % (SEASON, week, team)
        games.add((SEASON, week, game, team))
        tt = sum(p[2] for p in players.values())
        tc = sum(p[3] for p in players.values())
        totals[(game, team)] = (tt, tc)
        for pfr, (grp, pct, tgt, car) in players.items():
            present.add((pfr, SEASON, week))
            apps.append((SEASON, week, game, team, pfr, grp, pct, tgt, car))
    return apps, totals, present, games


BASE = {
    # WR1 takes 30% of targets; WR2 20%, WR3 10%; a TE and an RB fill the rest.
    "wr1": ("WR", 0.95, 12, 0),
    "wr2": ("WR", 0.90, 8, 0),
    "wr3": ("WR", 0.60, 4, 0),
    "te1": ("TE", 0.85, 8, 0),
    "rb1": ("RB", 0.60, 8, 20),
}


def _without(d, *keys, **extra):
    out = {k: v for k, v in d.items() if k not in keys}
    out.update(extra)
    return out


def test_a_role_player_missing_after_four_appearances_is_one_absence():
    weeks = {w: dict(BASE) for w in range(1, 5)}
    weeks[5] = _without(BASE, "wr1", wr2=("WR", 0.95, 14, 0),
                        wr3=("WR", 0.90, 10, 0), wr4=("WR", 0.50, 4, 0))
    ab, pl, _c = vacancy.find_events(*_season(weeks))
    assert [(e["pfr"], e["group"]) for e in ab] == [("wr1", "WR")]
    vac, slots, d_group, d_other = ab[0]["m"]["targets"]
    assert vac == pytest.approx(0.30)
    # week 5 team targets = 14+10+4+8+8 = 44
    assert slots[0] == pytest.approx(14 / 44 - 0.20)       # wr2, the top baseline
    assert slots[1] == pytest.approx(10 / 44 - 0.10)       # wr3
    assert slots[2] == pytest.approx(4 / 44 - 0.0)         # wr4: no baseline = 0
    assert d_group == pytest.approx(sum(slots))
    assert d_other == pytest.approx((8 / 44 - 0.2) * 2)    # te1 and rb1


def test_fewer_than_four_prior_appearances_is_not_an_absence():
    weeks = {w: dict(BASE) for w in range(1, 4)}
    weeks[4] = _without(BASE, "wr1")
    ab, _pl, _c = vacancy.find_events(*_season(weeks))
    assert ab == []


def test_any_row_at_all_means_he_played():
    """The brief's third lie: a returning or special-teams-only week has a
    snap row with zero offensive snaps, and it is not an absence."""
    weeks = {w: dict(BASE) for w in range(1, 5)}
    weeks[5] = _without(BASE, wr1=("WR", 0.0, 0, 0))
    ab, pl, _c = vacancy.find_events(*_season(weeks))
    assert ab == []
    assert any(e["pfr"] == "wr1" for e in pl)      # a present game is a placebo


def test_a_player_on_another_roster_that_week_is_traded_not_absent():
    weeks = {w: dict(BASE) for w in range(1, 5)}
    weeks[5] = _without(BASE, "wr1")
    apps, totals, present, games = _season(weeks)
    present.add(("wr1", SEASON, 5))                # dressed for someone else
    ab, _pl, counts = vacancy.find_events(apps, totals, present, games)
    assert ab == []
    assert counts["  on another roster that week (traded): dropped"] == 1


def test_only_the_first_game_of_a_spell_counts():
    weeks = {w: dict(BASE) for w in range(1, 5)}
    weeks[5] = _without(BASE, "wr1")
    weeks[6] = _without(BASE, "wr1")
    ab, _pl, _c = vacancy.find_events(*_season(weeks))
    assert [e["game"][:7] for e in ab] == ["2020_05"]


def test_a_team_game_with_two_qualifying_absences_is_dropped():
    weeks = {w: dict(BASE) for w in range(1, 5)}
    weeks[5] = _without(BASE, "wr1", "te1")
    ab, _pl, counts = vacancy.find_events(*_season(weeks))
    assert ab == []
    assert counts["  in a team-game with 2+ qualifying absences: dropped"] == 2


def test_a_player_below_both_role_thresholds_leaves_no_vacancy():
    small = _without(BASE, wr3=("WR", 0.35, 2, 0))  # ~5% targets, 35% snaps
    weeks = {w: dict(small) for w in range(1, 5)}
    weeks[5] = _without(small, "wr3")
    ab, _pl, _c = vacancy.find_events(*_season(weeks))
    assert ab == []
    # ...and the same player above the snap threshold does qualify
    big = _without(BASE, wr3=("WR", 0.45, 2, 0))
    weeks = {w: dict(big) for w in range(1, 5)}
    weeks[5] = _without(big, "wr3")
    ab, _pl, _c = vacancy.find_events(*_season(weeks))
    assert [e["pfr"] for e in ab] == ["wr3"]
    assert ab[0]["qual_snaps"] and not ab[0]["qual_targets"]


def test_slots_rank_by_own_preceding_share_not_by_any_label():
    """'wr3' out-targets 'wr2' over the baseline, so it is slot 1 whatever it
    is called."""
    swapped = _without(BASE, wr2=("WR", 0.90, 4, 0), wr3=("WR", 0.60, 8, 0))
    weeks = {w: dict(swapped) for w in range(1, 5)}
    weeks[5] = _without(swapped, "wr1", wr2=("WR", 0.9, 4, 0),
                        wr3=("WR", 0.9, 20, 0))
    ab, _pl, _c = vacancy.find_events(*_season(weeks))
    slots = ab[0]["m"]["targets"][1]
    total = 4 + 20 + 8 + 8
    assert slots[0] == pytest.approx(20 / total - 0.20)    # 'wr3'
    assert slots[1] == pytest.approx(4 / total - 0.10)     # 'wr2'


def test_the_metric_declares_a_team_denominator_and_the_snap_floor():
    for measure in vacancy.MEASURES:
        m = vacancy.metric_for(measure)
        assert m.shares_denominator == "team"
        assert m.floor_season == vacancy.SNAP_FIRST_SEASON == 2013
        assert "SELECTION IS NOT CORRECTED" in m.range_caveat
        assert m.block == "team" and m.subject_type == "league"


def test_the_pre_registered_thresholds_are_the_briefs():
    assert vacancy.ROLE_TARGET_SHARE == 0.15
    assert vacancy.ROLE_SNAP_SHARE == 0.40
    assert vacancy.BASELINE_APPEARANCES == 4


def test_the_published_intervals_reproduce_across_processes():
    """Block order once came from a set of strings, whose hash is salted per
    process, so two runs on the same data gave different intervals. Two
    subprocesses with different hash seeds must now agree to the last digit.
    The six teams DIFFER, or block order could not matter and this passed
    against the defect too - which it did, in its first version."""
    import os
    import subprocess
    import sys
    code = (
        "from tests.test_analytics_vacancy import _season, BASE, _without\n"
        "from analytics import vacancy\n"
        "weeks = {}\n"
        "for i, t in enumerate(('AAA', 'BBB', 'CCC', 'DDD', 'EEE', 'FFF')):\n"
        "    w = {k: dict(BASE) for k in range(1, 5)}\n"
        "    w[5] = _without(BASE, 'wr1', wr4=('WR', 0.5, 1 + 3 * i, 0))\n"
        "    a = _season(w, team=t)\n"
        "    for i, part in enumerate(a):\n"
        "        weeks.setdefault(i, type(part)())\n"
        "        weeks[i] = weeks[i] + part if isinstance(part, list) else "
        "(weeks[i].update(part) or weeks[i]) if isinstance(part, dict) else "
        "weeks[i] | part\n"
        "ab, pl, _c = vacancy.find_events(weeks[0], weeks[1], weeks[2], weeks[3])\n"
        "for s, sl, e in vacancy.aggregate(ab, pl, 'targets', 'vacancy.targets'):\n"
        "    print(sl, repr(e.est), repr(e.lo), repr(e.hi))\n")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    outs = []
    for seed in ("1", "2"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        outs.append(subprocess.run([sys.executable, "-c", code], cwd=root,
                                   env=env, capture_output=True, text=True,
                                   check=True).stdout)
    assert outs[0].strip(), "the probe printed nothing - it asserted nothing"
    assert "WR|slot1" in outs[0]
    assert outs[0] == outs[1]
