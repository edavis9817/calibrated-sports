"""The Lab universe: the as-of guard, settlement parity, and the fixed-settlement
prerequisite (AUDIT 6.4, 6.9, 6.10). pytest -q tests/test_lab_universe.py

THE AS-OF GUARD is the test 6.10 names first: shuffle and rewrite every row at
or after week w and assert that no feature for week w moves - and, so that the
guard can fail, that rewriting a row BEFORE week w does move one.

SETTLEMENT PARITY builds a store, settles it with jobs.settle_outcomes itself,
builds the universe from it, and asserts every universe outcome equals what
settle_one decides. The fixture is chosen to exercise the three cases that
matter: a played-with-no-stat-row zero (the fix), a zero-snap void, and the
pinned three-column tackles definition (5 without `def_tackles_with_assist`,
6 with it, against a 5.5 line - so the wrong definition flips the result).
"""
import os
import random
import sqlite3
import time

import pytest

import config
import store
from core.outcomes import Side, Stat, is_push_possible, player_prop
from lab import catalogue, features, universe

NOW = 1_790_000_000.0


# =============================================================================
# the as-of guard
# =============================================================================

def _hist(seed=0):
    rng = random.Random(seed)
    out = []
    for season in (2023, 2024):
        for week in range(1, 18):
            for gsis, pos, team in (("00-A", "WR", "AAA"), ("00-B", "WR", "BBB"),
                                    ("00-C", "RB", "AAA")):
                opp = {"AAA": "BBB", "BBB": "CCC"}[team] if week % 2 else "DDD"
                out.append({"gsis": gsis, "season": season, "week": week,
                            "position": pos, "team": team, "opponent": opp,
                            "target_share": rng.random() / 3,
                            "snap_off": rng.random(), "snap_def": 0.0,
                            "values": {"receptions": float(rng.randint(0, 9)),
                                       "rush_attempts": float(rng.randint(0, 20))}})
    return out


def _all_features(hist, season, week):
    pidx = features.PlayerIndex(hist)
    oidx = features.OpponentIndex(hist)
    out = {}
    for gsis in ("00-A", "00-B", "00-C"):
        for market in ("receptions", "rush_attempts"):
            for line in (2.5, 4.5, 11.5):
                f = features.player_features(pidx, gsis, season, week, market, line)
                pos = f["player.position"]
                f["opp.allowed_trailing"] = oidx.allowed(season, week, "BBB", pos, market)
                f["opp.allowed_rank_trailing"] = oidx.rank(season, week, "BBB", pos, market)
                out[(gsis, market, line)] = f
    return out


def _rewrite_from(hist, cutoff, seed):
    """Shuffle the list, rewrite every value at or after `cutoff`, and append
    extra future games. Only the future is touched."""
    rng = random.Random(seed)
    out = []
    for g in hist:
        g = dict(g, values=dict(g["values"]))
        if features.ordinal(g["season"], g["week"]) >= cutoff:
            g["values"] = {k: float(rng.randint(0, 30)) for k in g["values"]}
            g["position"] = rng.choice(["WR", "RB", "TE"])
            g["team"] = rng.choice(["AAA", "ZZZ"])
            g["snap_off"] = rng.random()
            g["target_share"] = rng.random()
        out.append(g)
    out.append({"gsis": "00-A", "season": 2025, "week": 1, "position": "QB",
                "team": "QQQ", "opponent": "BBB", "target_share": 0.9,
                "snap_off": 1.0, "snap_def": 0.0,
                "values": {"receptions": 50.0, "rush_attempts": 50.0}})
    rng.shuffle(out)
    return out


@pytest.mark.parametrize("season,week", [(2023, 6), (2024, 1), (2024, 9)])
def test_asof_no_feature_for_week_w_reads_week_w_or_later(season, week):
    hist = _hist()
    before = _all_features(hist, season, week)
    for seed in range(5):
        after = _all_features(_rewrite_from(hist, features.ordinal(season, week), seed),
                              season, week)
        assert after == before


def test_asof_guard_can_fail_a_prior_row_does_move_features():
    """The discrimination: rewriting the week BEFORE the target moves the
    trailing mean. Without this the test above could pass by computing
    nothing at all."""
    hist = _hist()
    before = _all_features(hist, 2023, 6)
    moved = [dict(g, values={k: v + 100 for k, v in g["values"].items()})
             if (g["season"], g["week"]) == (2023, 5) else g for g in hist]
    after = _all_features(moved, 2023, 6)
    assert after != before
    assert after[("00-A", "receptions", 4.5)]["player.mean_l3"] == pytest.approx(
        before[("00-A", "receptions", 4.5)]["player.mean_l3"] + 100 / 3)


def test_rest_days_read_only_the_previous_game():
    def g(gid, week, kick, home="AAA", away="BBB"):
        return {"game_id": gid, "season": 2024, "week": week, "kickoff_ts": kick,
                "home_team": home, "away_team": away, "spread_line": 3.0,
                "total_line": 44.5, "roof": "dome", "surface": "grass"}
    base = [g("g1", 1, NOW), g("g2", 2, NOW + 7 * 86400), g("g3", 3, NOW + 14 * 86400)]
    f = features.game_team_features(base)
    moved = base[:2] + [g("g3", 3, NOW + 9 * 86400, away="CCC")]
    f2 = features.game_team_features(moved)
    assert f[("g2", "AAA")] == f2[("g2", "AAA")]
    assert f[("g2", "AAA")]["game.rest_days"] == 7
    assert f[("g1", "AAA")]["game.spread"] == -3.0      # home favoured by 3
    assert f[("g1", "BBB")]["game.spread"] == 3.0
    assert f[("g1", "AAA")]["game.team_implied_total"] == pytest.approx(23.75)


def test_team_comes_from_the_last_prior_game_and_a_mismatch_is_none():
    hist = [{"gsis": "00-A", "season": 2024, "week": 1, "position": "WR",
             "team": "OLD", "opponent": "X", "values": {"receptions": 3.0}}]
    pidx = features.PlayerIndex(hist)
    f = features.player_features(pidx, "00-A", 2024, 2, "receptions", 4.5,
                                 game_teams=("NEW", "X2"))
    assert f["player.team"] is None
    f = features.player_features(pidx, "00-A", 2024, 2, "receptions", 4.5,
                                 game_teams=("OLD", "X2"))
    assert f["player.team"] == "OLD"


def test_fewer_than_n_prior_games_is_none_not_a_short_mean():
    hist = [{"gsis": "00-A", "season": 2024, "week": w, "position": "WR",
             "team": "T", "opponent": "O", "values": {"receptions": 5.0}}
            for w in (1, 2)]
    f = features.player_features(features.PlayerIndex(hist), "00-A", 2024, 3,
                                 "receptions", 4.5)
    assert f["player.mean_l3"] is None and f["player.cleared_l3"] is None
    assert f["player.streak"] == 2 and f["player.games_prior"] == 2


# =============================================================================
# the catalogue: ranges derived per market, through derive_range
# =============================================================================

def test_one_markets_refusal_does_not_grey_out_another_market():
    """Regression: ranging a stat feature over the union of markets let the
    tackles columns' refusal (upstream reclassification of
    def_tackles_with_assist) remove the feature from receptions too."""
    def derive(con, req):
        cols = {c for _d, c in req.requires}
        if "def_tackles_with_assist" in cols:
            raise SystemExit("whose last usable season is before the archive ends")
        return 1999, 2026, "fake"
    f = catalogue.ranges(None, {"prop": (2023, 2025)},
                         {"prop": ["receptions", "tackles_assists"]}, derive=derive)
    per = f["player.mean_l5"]["by_market"]
    assert per["receptions"]["availability"] == "historical"
    assert (per["receptions"]["season_from"], per["receptions"]["season_to"]) == (2023, 2025)
    assert per["tackles_assists"]["availability"] == "none"
    assert f["model.p"]["availability"] == "none"


def _analytics_db():
    p = config.storage_path("analytics.db")
    return p if os.path.exists(p) else None


@pytest.mark.skipif(_analytics_db() is None, reason="no scanned analytics.db on this machine")
def test_the_real_derive_range_accepts_the_catalogues_requirement_object():
    from analytics import paths
    from analytics.metrics import derive_range
    con = paths.connect(read_only=True)
    try:
        lo, hi, note = derive_range(con, catalogue._Req(
            "t", (("weekly_stats", "receptions"),)))
    finally:
        con.close()
    assert 1999 <= lo <= hi and isinstance(note, str)


# =============================================================================
# settlement parity, on a store settled by jobs.settle_outcomes itself
# =============================================================================

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path))
    store.init_db()
    yield tmp_path


GAMES = [("2024_%02d_BBB_AAA" % w, w) for w in (1, 2, 3, 4)]


def _seed(con_path):
    now = time.time()
    store.replace_rows(
        "nfl_games",
        ("sport", "game_id", "data_version", "season", "week", "game_type", "gameday",
         "kickoff_ts", "home_team", "away_team", "home_score", "away_score",
         "spread_line", "total_line", "source", "ingested_ts"),
        [("nfl", gid, "v1", 2024, w, "REG", "2024-09-0%d" % w, NOW + w * 7 * 86400,
          "AAA", "BBB", 24, 20, 3.0, 44.5, "test", now) for gid, w in GAMES])
    store.replace_rows(
        "player_xwalk", ("gsis_id", "display_name", "position", "last_season",
                         "status", "pfr_id", "ingested_ts"),
        [("00-WR", "Wide Out", "WR", 2024, "ACT", "WideWi00", now),
         ("00-LB", "Line Backer", "LB", 2024, "ACT", "LineLi00", now)])
    pw_cols = ("sport", "gsis_id", "season", "week", "season_type", "data_version",
               "position", "team", "opponent", "receptions", "def_tackles_solo",
               "def_tackles_with_assist", "def_tackle_assists", "source", "ingested_ts")
    store.replace_rows("nfl_player_week", pw_cols, [
        ("nfl", "00-WR", 2024, 1, "REG", "v1", "WR", "AAA", "BBB", 6, None, None, None, "t", now),
        ("nfl", "00-WR", 2024, 4, "REG", "v1", "WR", "AAA", "BBB", 5, None, None, None, "t", now),
        # 3 solo + 1 with_assist + 2 assists = 6 on the pinned definition, 5 without
        ("nfl", "00-LB", 2024, 2, "REG", "v1", "LB", "AAA", "BBB", None, 3, 1, 2, "t", now),
    ])
    store.replace_rows(
        "nfl_snap_counts",
        ("sport", "pfr_player_id", "game_id", "data_version", "season", "week",
         "position", "team", "offense_snaps", "offense_pct", "defense_snaps",
         "defense_pct", "source", "ingested_ts"),
        [("nfl", "WideWi00", GAMES[0][0], "v1", 2024, 1, "WR", "AAA", 40, 0.7, 0, 0, "t", now),
         # week 2: played 30 snaps, NO stat row -> actual 0, the over misses
         ("nfl", "WideWi00", GAMES[1][0], "v1", 2024, 2, "WR", "AAA", 30, 0.5, 0, 0, "t", now),
         # week 3: zero snaps -> void
         ("nfl", "WideWi00", GAMES[2][0], "v1", 2024, 3, "WR", "AAA", 0, 0, 0, 0, "t", now),
         ("nfl", "WideWi00", GAMES[3][0], "v1", 2024, 4, "WR", "AAA", 41, 0.7, 0, 0, "t", now),
         # the linebacker: week 1 defensive snaps and no row -> 0 tackles
         ("nfl", "LineLi00", GAMES[0][0], "v1", 2024, 1, "LB", "AAA", 0, 0, 55, 0.9, "t", now),
         ("nfl", "LineLi00", GAMES[1][0], "v1", 2024, 2, "LB", "AAA", 0, 0, 60, 0.95, "t", now)])
    claims = [("00-WR", 1, Stat.RECEPTIONS, 4.5), ("00-WR", 2, Stat.RECEPTIONS, 4.5),
              ("00-WR", 3, Stat.RECEPTIONS, 4.5), ("00-WR", 4, Stat.RECEPTIONS, 5.0),
              ("00-LB", 1, Stat.TACKLES_ASSISTS, 5.5), ("00-LB", 2, Stat.TACKLES_ASSISTS, 5.5)]
    quotes, mapping = [], []
    for gsis, week, stat, line in claims:
        gid = GAMES[week - 1][0]
        for side, am, p in ((Side.OVER, -115, 0.51), (Side.UNDER, -105, 0.49)):
            o = player_prop(2024, week, gsis, stat, line, side, event_id=gid)
            store.upsert_outcome(o, event_id=gid)
            for book in ("draftkings", "fanduel"):
                mid = "%s|%s|%s|%s|%g|%s" % (gid, stat.value, gsis, side.value, line, book)
                mapping.append(("oddsapi:" + book, mid, o.outcome_id, "test", 1.0, None, now))
                quotes.append((NOW + week * 7 * 86400 - 600, "nfl", "oddsapi:" + book, gid,
                               mid, "prop", gsis, line, side.value, am, p,
                               universe.SOURCE, now))
    con = sqlite3.connect(con_path)
    con.executemany("INSERT INTO market_outcome (venue, market_id, outcome_id, method, "
                    "confidence, unmapped_reason, mapped_ts) VALUES (?,?,?,?,?,?,?)", mapping)
    con.executemany("INSERT INTO quotes (ts, sport, venue, event_id, market_id, market_type, "
                    "subject, line, side, last, prob_devig, source, ingest_ts) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", quotes)
    con.commit()
    con.close()
    return claims


def test_universe_settles_exactly_as_settle_outcomes_does(env):
    from jobs import settle_outcomes
    _seed(config.DB_PATH)
    # BEFORE settling: the store carries no fixed settlement, and the build refuses.
    with pytest.raises(universe.UnfixedSettlement):
        universe.build(db=config.DB_PATH, dest=str(env / "lab"), verbose=False)

    settle_outcomes.run()
    u = universe.build(db=config.DB_PATH, dest=str(env / "lab"), verbose=False)
    rows = u["rows"].to_dicts()
    assert rows, "the universe is empty - nothing was compared"

    con = sqlite3.connect(config.DB_PATH)
    snaps, players, weeks = settle_outcomes.load_snap_index(con)
    compared = 0
    for oid, key, sport, season, week, etype, eid, stat, line, side, pp in con.execute(
            "SELECT outcome_id, key, sport, season, week, entity_type, entity_id, stat, "
            "line, side, push_possible FROM outcomes"):
        result, actual, _v, _r = settle_outcomes.settle_one(
            con, (oid, key, sport, season, week, etype, eid, stat, line, side, pp),
            snaps=snaps, coverage=(players, weeks))
        want = universe.side_outcome(result, side)
        claim = "%s|%s|%s|%s|%g" % (season, week, eid, stat, line)
        got = {r["outcome"] for r in rows if r["claim"] == claim and r["side"] == side}
        assert got == {want}, (claim, side, got, want)
        compared += 1
    con.close()
    assert compared == 12

    by = {(r["claim"], r["side"]): r for r in rows}
    # the three cases the fixture exists for
    assert by[("2024|2|00-WR|receptions|4.5", "over")]["outcome"] == "missed"
    assert by[("2024|2|00-WR|receptions|4.5", "over")]["actual"] == 0.0
    assert by[("2024|3|00-WR|receptions|4.5", "over")]["outcome"] == "void"
    assert by[("2024|2|00-LB|tackles_assists|5.5", "over")]["outcome"] == "cleared"
    assert by[("2024|2|00-LB|tackles_assists|5.5", "over")]["actual"] == 6.0
    assert by[("2024|4|00-WR|receptions|5", "over")]["outcome"] == "push"

    # the zero-outcome count per market, as the build reports it
    ev = u["meta"]["settlement"]["by_market"]
    assert ev["receptions"]["settled_with_no_stat_row"] == 1
    assert ev["receptions"]["zero_actual"] == 1
    assert ev["tackles_assists"]["settled_with_no_stat_row"] == 1
    assert u["meta"]["settlement_fixed"] is True


def test_the_as_of_features_on_built_rows_use_prior_weeks_only(env):
    from jobs import settle_outcomes
    _seed(config.DB_PATH)
    settle_outcomes.run()
    rows = universe.build(db=config.DB_PATH, dest=str(env / "lab"),
                          verbose=False)["rows"].to_dicts()
    wk = {r["week"]: r for r in rows
          if r["market"] == "receptions" and r["side"] == "over" and r["book"] == "draftkings"}
    # week 1 has no prior game; week 2 sees week 1 only (6 receptions)
    assert wk[1]["player.games_prior"] == 0 and wk[1]["player.streak"] is None
    assert wk[2]["player.games_prior"] == 1 and wk[2]["player.streak"] == 1
    # week 4 sees 6, then the zero-filled 0 of week 2 (week 3 had no snaps: no game)
    assert wk[4]["player.games_prior"] == 2 and wk[4]["player.streak"] == -1


# =============================================================================
# the live store: the prerequisite counts (6.9 item 1)
# =============================================================================

@pytest.mark.skipif(os.getenv("LOGGER_DB") is None and not os.path.isabs(str(config.DB_PATH)),
                    reason="no configured store (LOGGER_DB unset)")
def test_live_store_settles_zero_outcomes_in_every_market():
    if not os.path.exists(config.DB_PATH):
        pytest.skip("configured store %s does not exist" % config.DB_PATH)
    con = universe.connect_ro()
    try:
        ev = universe.settlement_evidence(con)
    finally:
        con.close()
    bm = ev["by_market"]
    assert set(bm) == set(universe.PROP_MARKETS), bm
    for m, v in bm.items():
        assert v["zero_actual"] > 0, (m, v)
        assert v["settled_with_no_stat_row"] > 0, (m, v)
    universe.require_fixed(ev)
