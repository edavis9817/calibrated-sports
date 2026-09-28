"""analytics.role - the down-and-distance family (a-40).

In-memory fixtures only: nothing here opens a store.
"""
import sqlite3

import pytest

from analytics import role


def _onfield(player_rows, team_rows):
    con = sqlite3.connect(":memory:")
    con.executescript(role.SCHEMA)
    con.executemany("INSERT INTO f_onfield_game VALUES (?,?,?,?,?,?)", player_rows)
    con.executemany("INSERT INTO f_onfield_team_game VALUES (?,?,?,?,?)", team_rows)
    return con


def _usage(rows):
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE f_play_usage (season, game_id, player_id, role, team, "
                "down_bucket, is_carry, is_target)")
    con.executemany("INSERT INTO f_play_usage VALUES (?,?,?,?,?,?,?,?)", rows)
    return con


# Two games. The team runs 10 third-and-short plays in each; the player is on
# the field for 5 of them in g1 and NONE in g2, though he plays 1st down in both.
FIXTURE = (
    [(2025, "g1", "GB", "p", "3rd_short", 5),
     (2025, "g1", "GB", "p", "1st", 20),
     (2025, "g2", "GB", "p", "1st", 20)],
    [(2025, "g1", "GB", "3rd_short", 10), (2025, "g1", "GB", "1st", 25),
     (2025, "g2", "GB", "3rd_short", 10), (2025, "g2", "GB", "1st", 25)])


def test_a_game_he_played_with_no_snap_in_the_bucket_counts_as_zero():
    blocks = role._onfield_blocks(_onfield(*FIXTURE), 2025, 2025)
    assert blocks[("p", "3rd_short")] == {"g1": (5, 10), "g2": (0, 10)}
    # 5 of 20, not 5 of 10
    share = sum(a for a, _b in blocks[("p", "3rd_short")].values()) / 20
    assert share == pytest.approx(0.25)


def test_the_old_construction_would_have_read_this_fixture_high():
    """The fixture discriminates: selecting games on the bucket reads 0.50."""
    con = _onfield(*FIXTURE)
    old = {}
    for g, n in con.execute("SELECT game_id, snaps FROM f_onfield_game "
                            "WHERE player_id='p' AND down_bucket='3rd_short'"):
        old[g] = n
    assert sum(old.values()) / (10 * len(old)) == pytest.approx(0.50)


def test_a_game_he_did_not_play_is_not_a_zero():
    player, team = FIXTURE
    team = team + [(2025, "g3", "GB", "3rd_short", 10)]
    blocks = role._onfield_blocks(_onfield(player, team), 2025, 2025)
    assert "g3" not in blocks[("p", "3rd_short")]


def test_a_bucket_his_team_never_ran_is_not_a_zero():
    player, team = FIXTURE
    blocks = role._onfield_blocks(_onfield(player, team), 2025, 2025)
    assert ("p", "4th") not in blocks


def test_touch_share_counts_zero_touch_games_too():
    con = _usage([
        (2025, "g1", "p", "receiver", "GB", "3rd_short", 0, 1),
        (2025, "g1", "q", "rusher", "GB", "3rd_short", 1, 0),
        (2025, "g2", "p", "receiver", "GB", "1st", 0, 1),
        (2025, "g2", "q", "rusher", "GB", "3rd_short", 1, 0),
    ])
    blocks = role._touch_blocks(con, 2025, 2025)
    assert blocks[("p", "3rd_short")] == {"g1": (1, 2), "g2": (0, 1)}


def test_by_season_slices_each_season_separately_with_a_cluster_t():
    player_rows, team_rows = [], []
    for season in (2024, 2025):
        for g in range(4):
            gid = "%d_g%d" % (season, g)
            snaps = 3 if season == 2024 else 6 + (g % 2)
            player_rows.append((season, gid, "GB", "p", "3rd_short", snaps))
            team_rows.append((season, gid, "GB", "3rd_short", 10))
    out = role.compute_by_season(_onfield(player_rows, team_rows), "onfield",
                                 2024, 2025)
    got = {sl: e for _p, sl, e in out}
    assert set(got) == {"2024|3rd_short", "2025|3rd_short"}
    assert got["2024|3rd_short"].est == pytest.approx(0.3)
    assert got["2025|3rd_short"].est == pytest.approx(0.65)
    for e in got.values():
        assert e.method == "cluster_t95" and e.n == 4
        assert 0.0 <= e.lo <= e.est <= e.hi <= 1.0
    # a zero-variance season is not certainty: it takes the [0, 1] bounds
    assert (got["2024|3rd_short"].lo, got["2024|3rd_short"].hi) == (0.0, 1.0)


def test_a_one_game_season_is_not_published():
    out = role.compute_by_season(
        _onfield([(2025, "g1", "GB", "p", "3rd_short", 5)],
                 [(2025, "g1", "GB", "3rd_short", 10)]),
        "onfield", 2025, 2025)
    assert out == []


def test_touch_share_requires_the_target_columns():
    reqs = role.metric_for("touch").requires
    assert ("pbp", "receiver_player_id", "incomplete_pass") in reqs
    assert role.metric_for("touch", by_season=True).requires == reqs


def test_the_by_season_metrics_declare_their_grain_and_denominator():
    for kind in role.KINDS:
        m = role.metric_for(kind, by_season=True)
        assert m.key == "role.%s_share.by_season" % kind
        assert m.slice_kind == role.SEASON_SLICE_KIND
        assert m.shares_denominator == "team"
        assert m.availability == role.metric_for(kind).availability
    assert role.season_slice(2025, "3rd_short") == "2025|3rd_short"
