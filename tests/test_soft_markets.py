"""c-33 - the soft-market survey's pure pieces. Synthetic rows only; no store is opened."""
import ast
import math
import os

import numpy as np
import polars as pl
import pytest

from research import soft_markets as sm

KICK = 1_000_000.0
SCHEMA = {"ts": pl.Float64, "venue": pl.Utf8, "event_id": pl.Utf8, "market_id": pl.Utf8,
          "line": pl.Float64, "raw": pl.Float64, "source_ts": pl.Float64}
K = pl.DataFrame({"event_id": ["E1", "E2"], "kick": [KICK, KICK]})


def live(rows):
    """rows: (lead_s, book, event, key, subj, side_name, line, raw)."""
    out = [(KICK - lead, "oddsapi:" + b, ev, "%s|%s|%s|%s" % (ev, key, subj, name), line, raw, KICK - lead - 5)
           for lead, b, ev, key, subj, name, line, raw in rows]
    return sm.normalise(pl.DataFrame(out, schema=SCHEMA, orient="row"), K, "L")


def two(lead, book, ev, key, subj, line, o, u):
    return [(lead, book, ev, key, subj, "Over", line, o), (lead, book, ev, key, subj, "Under", line, u)]


def test_hold_and_devig():
    h, p = sm.hold_and_p(0.55, 0.52)
    assert h == pytest.approx(0.07) and p == pytest.approx(0.55 / 1.07)
    assert sm.hold_and_p(0.999, 0.999) is None          # pointsbetus: both sides ~1
    assert sm.hold_and_p(0.48, 0.50) is None            # no overround is not a book price
    assert sm.hold_and_p(None, 0.5) is None


def test_pairs_keep_two_sided_only_and_drop_broken():
    n = live(two(600, "dk", "E1", "player_receptions", "A B", 4.5, 0.55, 0.52)
             + two(600, "pb", "E1", "player_receptions", "A B", 4.5, 0.999, 0.999)
             + [(600, "dk", "E1", "player_anytime_td", "A B", "Yes", None, 0.30)]
             + [(600, "dk", "E1", "player_receptions_alternate", "A B", "Over", 2.5, 0.85)]
             + [(600, "fd", "E1", "player_receptions", "C D", "Over", 3.5, 0.5)])
    p, counts = sm.pairs(n)
    assert p.height == 1 and p["book"][0] == "dk"
    assert p["hold"][0] == pytest.approx(0.07)
    assert counts == {"groups": 3, "two_sided": 2, "valid": 1}


def test_spreads_and_h2h_pair_the_two_teams():
    rows = [(600, "dk", "E1", "spreads", "", "Bears", -3.5, 0.53), (600, "dk", "E1", "spreads", "", "Jets", 3.5, 0.52),
            (600, "dk", "E1", "h2h", "", "Bears", None, 0.65), (600, "dk", "E1", "h2h", "", "Jets", None, 0.39)]
    p, _ = sm.pairs(live(rows))
    d = {r["key"]: r for r in p.iter_rows(named=True)}
    assert d["spreads"]["pline"] == 3.5 and d["spreads"]["o"] == 0.53     # A = the side laying points
    assert d["h2h"]["hold"] == pytest.approx(0.04)


def test_rows_at_or_after_kickoff_are_not_used():
    n = live(two(-60, "dk", "E1", "player_receptions", "A B", 4.5, 0.55, 0.52))
    assert n.height == 0


def test_close_is_last_read_inside_the_window_and_main_line_is_nearest_even():
    n = live(two(9000, "dk", "E1", "player_receptions", "A B", 4.5, 0.60, 0.47)
             + two(600, "dk", "E1", "player_receptions", "A B", 4.5, 0.70, 0.37)
             + two(600, "dk", "E1", "player_receptions", "A B", 5.5, 0.54, 0.53))
    p, _ = sm.pairs(n)
    lead = pl.col("kick") - pl.col("ts")
    c = sm.main_at(p, lead <= sm.CLOSE_MAX_LEAD)
    assert c.height == 1 and c["pline"][0] == 5.5 and c["ts"][0] == KICK - 600
    # a claim whose only read is outside the window has no close
    far = sm.main_at(sm.pairs(live(two(9000, "dk", "E1", "player_receptions", "A B", 4.5, 0.6, 0.47)))[0],
                     lead <= sm.CLOSE_MAX_LEAD)
    assert far.height == 0


def test_kappa_from_pairs_recovers_a_slope_and_refuses_thin_or_wrong_signed():
    rng = np.random.default_rng(1)
    dl = rng.uniform(-0.3, 0.3, 200)
    assert sm.kappa_from_pairs(-0.5 * dl, dl) == pytest.approx(0.5)
    assert sm.kappa_from_pairs(-0.5 * dl[:10], dl[:10]) is None
    assert sm.kappa_from_pairs(+0.5 * dl, dl) is None


def _yards_world(true_kappa=0.5, n_players=40):
    """Books share one price template and express a different belief by MOVING THE LINE."""
    rng = np.random.default_rng(7)
    rows = []
    for i in range(n_players):
        subj = "P%d" % i
        base = 50.0 * math.exp(rng.normal(0, 0.2))
        for b, shade in (("dk", 0.0), ("fd", 0.06), ("mgm", -0.05)):
            line = round(base * math.exp(shade)) + 0.5
            rows += two(600, b, "E1", "player_reception_yds", subj, line, 0.53, 0.53)
            for step in (-0.2, 0.2):                       # the book's own alternate ladder
                al = line * math.exp(step)
                rows.append((600, b, "E1", "player_reception_yds_alternate", subj, "Over", al,
                             (0.5 - true_kappa * step) * 1.06))
    return live(rows)


def test_cross_book_kappa_is_zero_when_books_move_the_line_and_the_ladder_recovers_it():
    # addendum 1: the registered estimator reads ~0 here; the ladder reads the true slope
    n = _yards_world()
    p, _ = sm.pairs(n)
    close = sm.main_at(p, (pl.col("kick") - pl.col("ts")) <= sm.CLOSE_MAX_LEAD)
    bp = sm.book_pairs(close)
    cross = sm.kappas(bp, ["player_reception_yds"])["player_reception_yds"]
    assert cross is None or cross < 0.01
    lad = sm.ladder_kappa(n, close)["player_reception_yds"]
    assert lad["claims"] == 120 and lad["kappa"] == pytest.approx(0.5, abs=1e-6)
    # and the disagreement it implies is the line gap, which the cross-book figure hides
    with_ladder, _ = sm.disagreement(close, {"player_reception_yds": lad["kappa"]})
    assert with_ladder["combined"].mean() > 0.02
    assert with_ladder["same_line"].drop_nulls().len() == 0 or with_ladder["same_line"].max() < 1e-9


def test_disagreement_by_hand():
    n = live(two(600, "dk", "E1", "player_receptions", "A B", 4.5, 0.55, 0.52)      # p = .5140
             + two(600, "fd", "E1", "player_receptions", "A B", 4.5, 0.50, 0.57)    # p = .4673
             + two(600, "mgm", "E1", "player_receptions", "A B", 5.5, 0.535, 0.535))  # p = .5
    p, _ = sm.pairs(n)
    close = sm.main_at(p, (pl.col("kick") - pl.col("ts")) <= sm.CLOSE_MAX_LEAD)
    cl, _ = sm.disagreement(close, {"player_receptions": 0.6})
    r = cl.row(0, named=True)
    pd_, pf, pm = 0.55 / 1.07, 0.50 / 1.07, 0.5
    assert r["n_books"] == 3
    assert r["same_line"] == pytest.approx(abs(pd_ - pf))
    assert r["split"] == pytest.approx(2 / 3)
    g = 0.6 * (math.log(5.5) - math.log(4.5))
    want = (abs(pd_ - pf) + abs(pd_ - pm - g) + abs(pf - pm - g)) / 3
    assert r["combined"] == pytest.approx(want)


def test_a_line_move_at_an_unchanged_price_is_movement():
    n = live(two(30 * 3600, "dk", "E1", "player_rush_yds", "A B", 50.5, 0.53, 0.53)
             + two(600, "dk", "E1", "player_rush_yds", "A B", 55.5, 0.53, 0.53))
    p, _ = sm.pairs(n)
    lead = pl.col("kick") - pl.col("ts")
    mv = sm.movement(sm.main_at(p, (lead >= sm.OPEN_MIN_LEAD) & (lead <= sm.OPEN_MAX_LEAD)),
                     sm.main_at(p, lead <= sm.CLOSE_MAX_LEAD), {"player_rush_yds": 0.5})
    r = mv.row(0, named=True)
    assert r["changed"] and r["same_move"] is None
    assert r["move"] == pytest.approx(0.5 * math.log(55.5 / 50.5))
    # with no kappa the line move is invisible: the figure is null, not zero
    blind = sm.movement(sm.main_at(p, (lead >= sm.OPEN_MIN_LEAD) & (lead <= sm.OPEN_MAX_LEAD)),
                        sm.main_at(p, lead <= sm.CLOSE_MAX_LEAD), {"player_rush_yds": None})
    assert blind["move"][0] is None


def test_bootstrap_is_over_games_and_keys_share_draws():
    v = np.array([1.0, 2.0, 3.0, np.nan, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])
    e1, d1 = sm.boot_means([v], len(v), draws=500)
    e2, d2 = sm.boot_means([v], len(v), draws=500)
    assert e1 == pytest.approx(np.nanmean(v)) and np.array_equal(d1, d2)      # seeded
    # a game's value is one number however many rows built it: 20x the rows inside each
    # game leaves the per-game means, and so the interval, exactly where they were
    rows = pl.DataFrame({"event_id": ["g%d" % i for i in range(10) if i != 3],
                         "key": ["k"] * 9, "x": [float(a) for a in v if np.isfinite(a)]})
    games = ["g%d" % i for i in range(10)]
    one = sm.game_vec(rows, "x", games, "k")
    many = sm.game_vec(pl.concat([rows] * 20), "x", games, "k")
    assert np.array_equal(one, many, equal_nan=True)
    # two keys on the same call see the same resampled games: a key that is another
    # plus a constant keeps the constant in every draw
    _, da = sm.boot_means([v], len(v), draws=500)
    _, db = sm.boot_means([v + 1.0], len(v), draws=500)
    assert np.allclose(db - da, 1.0)
    # hold: mean over books of each book's across-game mean
    e, _ = sm.boot_means([np.array([1.0, 1.0]), np.array([3.0, np.nan])], 2, draws=10)
    assert e == pytest.approx(2.0)


def test_ranks():
    assert list(sm.ranks_desc([3.0, 1.0, 2.0])) == [1.0, 3.0, 2.0]
    r = sm.ranks_desc([1.0, np.nan, 1.0])
    assert r[0] == r[2] == 1.5 and np.isnan(r[1])


def test_summarise_ranks_only_keys_with_enough_games():
    games = ["g%d" % i for i in range(12)]
    full = np.arange(12, dtype=float)
    thin = np.full(12, np.nan)
    thin[:4] = 100.0
    rows, _ = sm.summarise("t", {"a": [full], "b": [full + 1], "c": [thin]}, games, "high", ("a", "b", "c"))
    assert rows["b"]["rank"] == 1 and rows["a"]["rank"] == 2
    assert "rank" not in rows["c"] and rows["c"]["read"] is False
    low, _ = sm.summarise("t", {"a": [full], "b": [full + 1]}, games, "low", ("a", "b"))
    assert low["a"]["rank"] == 1                       # direction can produce the other answer


def test_the_script_can_make_no_request():
    """Credit budget 0: the module imports no HTTP client and no venue."""
    src = open(os.path.join(os.path.dirname(sm.__file__), "soft_markets.py"), encoding="utf-8").read()
    names = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add((node.module or "").split(".")[0])
    assert names, "no imports found: the walk read nothing"
    assert not names & {"httpx", "requests", "urllib", "aiohttp", "venues", "jobs", "http", "socket"}, names
    assert "mode=ro" in src and "sqlite3.connect(" in src
