"""Drafting strengths (F07): every test here is shown returning BOTH answers.

A separation test that can only say "no skill" is decoration, so each
inference function is driven on pure noise AND on a planted effect, and the
walk-forward is driven with a signal it may see and one it must not.
"""
import numpy as np
import polars as pl
import pytest

from analytics import drafting as dr
from analytics import paths

TEAMS = [f"T{i:02d}" for i in range(32)]


def synth(classes=12, picks_per_team=8, effect=0.0, seed=1, shared_pairs=False):
    """A league: every team picks `picks_per_team` times per class, y is
    slot value plus noise plus `effect` x a per-team skill."""
    rng = np.random.default_rng(seed)
    skill = rng.normal(0, 1, len(TEAMS))
    rows = []
    for c in range(2000, 2000 + classes):
        if shared_pairs:
            # skill that lives for exactly two classes, then is redrawn
            if (c - 2000) % 2 == 0:
                skill = rng.normal(0, 1, len(TEAMS))
        order = [t for _ in range(picks_per_team) for t in range(len(TEAMS))]
        for pick, t in enumerate(order, 1):
            y = 10 / np.sqrt(pick) + rng.normal(0, 1) + effect * skill[t]
            rows.append({"season": c, "pick": pick, "franchise": TEAMS[t], "y": y})
    return pl.DataFrame(rows)


# ---------------------------------------------------------------------------
# the null is a typical team
# ---------------------------------------------------------------------------

def test_residuals_sum_to_zero_within_every_class():
    f = dr.residualize(synth(), "y")
    sums = f.group_by("season").agg(pl.col("resid").sum())["resid"]
    assert sums.abs().max() < 1e-9


def test_draft_capital_is_not_credited_as_skill():
    """A team holding every top pick and no skill must sit at ~0: the slot
    curve absorbs where you pick, and only value OVER the slot counts."""
    rng = np.random.default_rng(3)
    rows = []
    for c in range(2000, 2010):
        for pick in range(1, 257):
            team = "RICH" if pick <= 32 else f"T{pick % 31:02d}"
            rows.append({"season": c, "pick": pick, "franchise": team,
                         "y": 50 / np.sqrt(pick) + rng.normal(0, 1)})
    f = dr.residualize(pl.DataFrame(rows), "y")
    rich = f.filter(pl.col("franchise") == "RICH")
    raw_gap = rich["y"].mean() - f["y"].mean()
    assert raw_gap > 5                        # the raw number would call it skill
    assert abs(rich["resid"].mean()) < 0.5    # the residual does not


def test_class_relative_removes_a_level_shift_between_classes():
    """Career totals of an old class dwarf a young one's; dividing by the
    class mean is what makes them comparable."""
    df = synth().with_columns(
        (pl.col("y").abs() * (1 + (pl.col("season") - 2000))).alias("y"))
    f = dr.residualize(df, "y", class_relative=True)
    per = f.group_by("season").agg(pl.col("resid").std())["resid"]
    assert per.max() / per.min() < 3


# ---------------------------------------------------------------------------
# separation, both answers
# ---------------------------------------------------------------------------

def test_separation_does_not_reject_pure_noise():
    s = dr.separation(dr.residualize(synth(effect=0.0), "y"), perms=400)
    assert s["p"] > 0.05
    assert s["signal_share"] < 0.4


def test_separation_rejects_a_planted_skill():
    s = dr.separation(dr.residualize(synth(effect=0.5), "y"), perms=400)
    assert s["p"] < 0.01
    assert s["signal_share"] > 0.5


def test_null_exclusion_count_is_measured_not_assumed():
    """The chance count of intervals excluding 0 is computed under shuffled
    labels. It must be a real count (not the nominal 1.6) and must stay well
    under what a planted skill produces."""
    f0 = dr.residualize(synth(effect=0.0), "y")
    ne = dr.null_exclusions(f0, perms=30, draws=300)
    assert ne["perms"] == 30 and 0 <= ne["mean"] < 8
    f1 = dr.residualize(synth(effect=0.5), "y")
    planted = sum(t["excludes_null"] for t in dr.team_intervals(f1, 500))
    assert planted > ne["p95"]


# ---------------------------------------------------------------------------
# block bootstrap by class, not by pick
# ---------------------------------------------------------------------------

def test_duplicating_picks_inside_a_class_does_not_narrow_the_interval():
    """Brief 018's guard, moved to draft classes: twenty copies of every pick
    add no information, so an interval that narrows is resampling picks."""
    f = dr.residualize(synth(), "y")
    base = {t["team"]: t["hi"] - t["lo"] for t in dr.team_intervals(f, 800)}
    dup = pl.concat([f] * 20)
    wide = {t["team"]: t["hi"] - t["lo"] for t in dr.team_intervals(dup, 800)}
    ratio = np.median([wide[t] / base[t] for t in base])
    assert ratio > 0.85
    assert all(t["n"] == 12 for t in dr.team_intervals(dup, 50))


def test_team_draws_are_independent_across_teams():
    """Shared-denominator rule: one team's resampled classes must not be
    every team's. Two teams with identical data get different intervals."""
    f = dr.residualize(synth(), "y")
    a = f.filter(pl.col("franchise") == "T00")
    twin = pl.concat([a, a.with_columns(pl.lit("T99").alias("franchise"))])
    ti = {t["team"]: t for t in dr.team_intervals(twin, 400)}
    assert ti["T00"]["est"] == ti["T99"]["est"]
    assert (ti["T00"]["lo"], ti["T00"]["hi"]) != (ti["T99"]["lo"], ti["T99"]["hi"])


# ---------------------------------------------------------------------------
# walk-forward: as-of, both answers
# ---------------------------------------------------------------------------

def test_walk_forward_finds_a_persistent_skill():
    wf = dr.walk_forward(dr.residualize(synth(classes=16, effect=0.5), "y"),
                         lag=4, draws=500)
    assert wf["readable"] and wf["lo"] > 0.2


def test_walk_forward_cannot_see_the_classes_whose_horizon_is_open():
    """Skill that lasts two classes is visible at lag 1 and invisible at lag
    4. If lag 4 found it, the predictor would be reading classes a front office
    could not yet have graded - look-ahead."""
    df = dr.residualize(synth(classes=20, effect=0.8, shared_pairs=True), "y")
    near = dr.walk_forward(df, lag=1, min_prior=1, draws=500)
    far = dr.walk_forward(df, lag=4, draws=500)
    assert near["r"] > 0.1
    assert far["lo"] <= 0 <= far["hi"]
    for row in far["rows"]:
        assert row["prior_classes"] == row["target"] - 2000 - 4 + 1


# ---------------------------------------------------------------------------
# survivorship: picks that never play are scored, never dropped
# ---------------------------------------------------------------------------

def _picks():
    return pl.DataFrame({
        "season": [2010, 2010, 2010, 2010],
        "pick": [1, 2, 3, 4],
        "team": ["OAK", "NWE", "NWE", "NWE"],
        "gsis_id": ["A", "B", "C", None],
        "pfr_player_id": ["pa", "pb", "pc", "pd"],
    })


def _rosters():
    rows = []
    for season in range(2010, 2015):
        for week in range(1, 18):
            rows.append((season, week, "LV", "A", None, "ACT"))   # 5 seasons
            rows.append((season, week, "NE", "B", None, "DEV"))   # practice squad
            if season == 2011:
                rows.append((season, week, "NE", "C", None, "RES"))  # on IR
    return pl.DataFrame(rows, schema=["season", "week", "team", "gsis_id",
                                      "pfr_id", "status"], orient="row")


def test_roster4_scores_every_pick_including_the_ones_who_never_played():
    out = dr.roster4(_picks(), _rosters()).sort("pick")
    assert out.height == 4                                   # nobody dropped
    assert out["roster_weeks"].to_list() == [68, 0, 17, 0]   # 4 seasons, not 5
    assert out["possible_weeks"].to_list() == [68] * 4
    assert out["bust"].to_list() == [0.0, 1.0, 0.0, 1.0]
    # OAK drafted A; A spent the horizon with LV - the same franchise.
    assert out["own_weeks"].to_list()[0] == 68


def test_every_team_code_in_every_feed_resolves_to_a_franchise():
    """The roster feed spells teams three ways (PFR GNB, nflverse GB, GSIS
    ARZ/BLT/CLV/HST/SL). A code that fails to map must refuse, not become a
    thirty-third team or silently never equal the drafting team."""
    with pytest.raises(ValueError, match="XYZ"):
        dr.check_franchises(pl.DataFrame({"team": ["GB", "XYZ"]}), "team")
    codes = ["GB", "GNB", "ARZ", "BLT", "CLV", "HST", "SL", "STL", "LA", "OAK",
             "LV", "RAI", "SD", "SDG", "PHO"]
    got = pl.DataFrame({"team": codes}).select(dr.to_franchise("team"))["team"]
    assert got.to_list() == ["GNB", "GNB", "ARI", "BAL", "CLE", "HOU", "LAR",
                             "LAR", "LAR", "LVR", "LVR", "LVR", "LAC", "LAC", "ARI"]


def test_practice_squad_and_cut_are_not_roster_weeks():
    assert "DEV" not in dr.ON_ROSTER and "CUT" not in dr.ON_ROSTER
    assert "RES" in dr.ON_ROSTER   # the season-stamp defect: see the module


def test_bh_keeps_the_step_up_set():
    assert dr.bh([0.001, 0.02, 0.9], q=0.10) == [True, True, False]
    assert dr.bh([0.5, 0.6, 0.9], q=0.10) == [False, False, False]


def test_bands_split_off_a_planted_leader_and_not_noise_everywhere():
    f = dr.residualize(synth(effect=0.0), "y")
    f = f.with_columns(pl.when(pl.col("franchise") == "T05")
                       .then(pl.col("resid") + 3).otherwise(pl.col("resid"))
                       .alias("resid"))
    b = dr.bands(f, draws=400)
    assert b[0] == ["T05"]
    assert sum(len(x) for x in b) == 32
    assert all(x == sorted(x) for x in b)


def test_environment_reports_the_correlation_it_is_given():
    f = dr.residualize(synth(effect=0.5), "y")
    means = {t["team"]: t["est"] for t in dr.team_intervals(f, 50)}
    ev = dr.environment(f, means)                 # win share == the residual
    assert ev["r"] > 0.999
    flipped = dr.environment(f, {k: -v for k, v in means.items()})
    assert flipped["r"] < -0.999


# ---------------------------------------------------------------------------
# the real archive: the figures F07 quotes
# ---------------------------------------------------------------------------

HAS_MIRROR = False
try:
    HAS_MIRROR = (paths.latest_asset(dr.DRAFT_ASSET) is not None
                  and len(paths.seasonal_files(dr.ROSTER_PATTERN)) >= 24)
except FileNotFoundError:
    HAS_MIRROR = False
needs_mirror = pytest.mark.skipif(
    not HAS_MIRROR, reason="no nflverse mirror with draft_picks and roster_weekly")


@needs_mirror
def test_roster_status_before_2016_is_a_season_stamp():
    """The defect that forced roster4 onto PRESENCE. Stephen Hill started as
    a 2012 rookie and is RES every week of that season. If upstream ever
    backfills real weekly statuses this fails, and `ON_ROSTER` should be
    revisited rather than this test edited."""
    r = dr.load_rosters(2012, 2012).filter(pl.col("gsis_id") == "00-0028995")
    assert r.height >= 10
    assert r["status"].unique().to_list() == ["RES"]


@needs_mirror
def test_picks_without_any_resolvable_id_are_counted():
    p = dr.load_picks()
    no_gsis = p.filter(pl.col("gsis_id").is_null())
    played = no_gsis.filter(pl.col("games").is_not_null() & (pl.col("games") > 0))
    assert no_gsis.height == 219
    assert played.height == 9
