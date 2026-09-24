"""The main line: one posted line per game. Run: pytest -q tests/test_prop_main_line.py

WHY. `prop_history.stats[].rate` pools every ladder rung, and a ladder's low rungs
nearly always clear while its high ones nearly always miss, so the pooled rate sits
near 50% whatever the player does. The main line is the one rung the close priced
nearest a coin flip. Every test here shows the rule returning the OTHER answer on
the other input, because a rule that picks "a line" passes any test that only asks
whether a line was picked.
"""
import sqlite3

from jobs import export_web as E

KICK = 10_000.0


def store(tmp_path):
    con = sqlite3.connect(tmp_path / "m.db")
    con.executescript("""
        CREATE TABLE outcomes (outcome_id TEXT, key TEXT, sport TEXT, season INT,
            week INT, entity_type TEXT, entity_id TEXT, stat TEXT, line REAL,
            side TEXT, push_possible INT, event_id TEXT, created_ts REAL);
        CREATE TABLE outcome_settlement (outcome_id TEXT, data_version TEXT,
            result TEXT, actual REAL, source TEXT, settled_ts REAL);
        CREATE TABLE outcome_close (outcome_id TEXT, close_ts REAL, kickoff_ts REAL,
            lead_min REAL, p_bench REAL, n_bench INT, p_all REAL, n_all INT,
            dispersion REAL, built_ts REAL);
        CREATE TABLE market_outcome (venue TEXT, market_id TEXT, outcome_id TEXT,
            method TEXT, confidence REAL, unmapped_reason TEXT, mapped_ts REAL);
        CREATE TABLE quotes (ts REAL, venue TEXT, market_id TEXT, side TEXT,
            best_bid REAL, best_ask REAL);
    """)
    return con


def outcome(con, oid, line, result, actual, *, season=2025, week=1, gsis="00-A",
            stat="receptions", side="over", game="G1", version="v1"):
    con.execute("INSERT INTO outcomes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (oid, oid, "nfl", season, week, "player", gsis, stat, line, side, 0, game, 0.0))
    con.execute("INSERT INTO outcome_settlement VALUES (?,?,?,?,?,?)",
                (oid, version, result, actual, "nflverse", 0.0))


def close(con, oid, p_bench, n_bench=3, p_all=None, n_all=None):
    con.execute("INSERT INTO outcome_close VALUES (?,?,?,?,?,?,?,?,?,?)",
                (oid, KICK - 600, KICK, 10.0, p_bench, n_bench,
                 p_bench if p_all is None else p_all, n_bench if n_all is None else n_all,
                 0.0, 0.0))


def quote(con, venue, mid, oid, ts, bid, ask):
    con.execute("INSERT OR IGNORE INTO market_outcome VALUES (?,?,?,?,?,?,?)",
                (venue, mid, oid, "t", 1.0, None, 0.0))
    con.execute("INSERT INTO quotes VALUES (?,?,?,?,?,?)", (ts, venue, mid, "yes", bid, ask))


GAMES = {"G1": {"game_id": "G1", "gameday": "2025-09-07", "home_team": "MIN",
                "away_team": "CHI", "kickoff_ts": KICK},
         "G2": {"game_id": "G2", "gameday": "2025-09-14", "home_team": "GB",
                "away_team": "MIN", "kickoff_ts": KICK}}
TEAM = {("00-A", 2025, 1): "MIN", ("00-A", 2025, 2): "MIN"}


def run(con, scope=("00-A",), season=2025):
    out, census = E.load_main_lines(con, set(scope), GAMES, TEAM, season)
    return out, census


def only_game(out):
    (g,) = out["00-A"]["games"]
    return g


# ------------------------------------------------------------- which line

def test_the_rung_nearest_even_money_is_the_main_line(tmp_path):
    con = store(tmp_path)
    for oid, line, p, res in (("a", 3.5, 0.80, "over"), ("b", 4.5, 0.55, "over"),
                              ("c", 5.5, 0.35, "under"), ("d", 6.5, 0.15, "under")):
        outcome(con, oid, line, res, 5)
        close(con, oid, p)
    g = only_game(run(con)[0])
    assert (g["line"], g["result"], g["p_over"]) == (4.5, "cleared", 0.55)
    # the other answer: move the price and the choice moves with it
    con.execute("UPDATE outcome_close SET p_bench = 0.49 WHERE outcome_id = 'c'")
    assert only_game(run(con)[0])["line"] == 5.5


def test_an_under_rows_close_is_flipped_to_the_over(tmp_path):
    """p_bench is de-vigged for the row's OWN side. An under priced 0.30 is an
    over at 0.70, which is FURTHER from 0.5 than a 0.45 over."""
    con = store(tmp_path)
    outcome(con, "u", 4.5, "over", 5, side="under")
    close(con, "u", 0.30)
    outcome(con, "o", 5.5, "under", 5)
    close(con, "o", 0.45)
    g = only_game(run(con)[0])
    assert g["line"] == 5.5
    con.execute("UPDATE outcome_close SET p_bench = 0.52 WHERE outcome_id = 'u'")  # over 0.48
    assert only_game(run(con)[0])["line"] == 4.5


def test_ties_go_to_the_lower_line(tmp_path):
    con = store(tmp_path)
    outcome(con, "hi", 5.5, "under", 5)
    close(con, "hi", 0.40)
    outcome(con, "lo", 4.5, "over", 5)
    close(con, "lo", 0.60)
    assert only_game(run(con)[0])["line"] == 4.5


def test_quarter_lines_are_never_candidates(tmp_path):
    con = store(tmp_path)
    outcome(con, "q", 0.75, "over", 1, stat="sacks")
    close(con, "q", 0.50)
    outcome(con, "h", 0.5, "over", 1, stat="sacks")
    close(con, "h", 0.70)
    g = only_game(run(con)[0])
    assert g["line"] == 0.5 and E.quarter_line(0.75) and E.quarter_line(0.25)
    assert not E.quarter_line(0.5) and not E.quarter_line(4.0)


def test_benchmark_books_first_then_every_book(tmp_path):
    con = store(tmp_path)
    outcome(con, "x", 4.5, "over", 5)
    close(con, "x", None, n_bench=0, p_all=0.5, n_all=2)
    g = only_game(run(con)[0])
    assert (g["provider"], g["books"]) == ("books_all", 2)


# ------------------------------------------------------------ the exchanges

def test_books_beat_the_exchange_and_are_never_mixed_with_it(tmp_path):
    """A Kalshi rung at exactly 0.50 must NOT win over a book rung at 0.40: the
    tiers are compared inside one provider's ladder, never across."""
    con = store(tmp_path)
    outcome(con, "b", 4.5, "over", 5)
    close(con, "b", 0.60)
    outcome(con, "k", 5.5, "under", 5)
    quote(con, "kalshi", "K55", "k", KICK - 60, 0.49, 0.51)
    g = only_game(run(con)[0])
    assert (g["line"], g["provider"]) == (4.5, "books_benchmark")
    con.execute("DELETE FROM outcome_close")
    g = only_game(run(con)[0])
    assert (g["line"], g["provider"], g["books"], g["p_over"]) == (5.5, "kalshi", None, 0.5)
    assert g["close_ts"] == KICK - 60


def test_exchange_close_is_the_LAST_quote_not_the_last_usable_one(tmp_path):
    con = store(tmp_path)
    outcome(con, "k", 5.5, "under", 5)
    quote(con, "kalshi", "K", "k", KICK - 300, 0.48, 0.52)
    quote(con, "kalshi", "K", "k", KICK - 60, None, 0.52)      # one-sided at the close
    out, census = run(con)
    assert out == {} and census["game_no_close_price"] == 1
    quote(con, "kalshi", "K", "k", KICK - 30, 0.47, 0.53)
    assert only_game(run(con)[0])["line"] == 5.5


def test_an_empty_polymarket_book_and_a_stale_quote_are_not_closes(tmp_path):
    con = store(tmp_path)
    outcome(con, "p", 0.5, "over", 1, stat="anytime_td")
    quote(con, "polymarket", "P", "p", KICK - 60, 0.0, 1.0)
    assert run(con)[0] == {}
    con.execute("DELETE FROM quotes")
    quote(con, "polymarket", "P", "p", KICK - 3 * 3600, 0.30, 0.34)
    assert run(con)[0] == {}
    quote(con, "polymarket", "P", "p", KICK - 120, 0.30, 0.34)
    assert only_game(run(con)[0])["provider"] == "polymarket"


def test_a_quote_at_or_after_kickoff_is_not_the_close(tmp_path):
    con = store(tmp_path)
    outcome(con, "k", 5.5, "under", 5)
    quote(con, "kalshi", "K", "k", KICK, 0.48, 0.52)
    assert run(con)[0] == {}


# ------------------------------------------------------- results and rates

def test_push_and_void_are_shown_and_counted_in_no_rate(tmp_path):
    con = store(tmp_path)
    outcome(con, "a", 5.0, "push", 5, week=1, game="G1")
    close(con, "a", 0.5)
    outcome(con, "b", 4.5, "void", None, week=2, game="G2")
    close(con, "b", 0.5)
    out, _ = run(con)
    rows = out["00-A"]["games"]
    assert [r["result"] for r in rows] == ["push", "void"]
    (m,) = out["00-A"]["main"]
    assert m["career"] == {"cleared": 0, "n": 0, "rate": None, "interval": None}
    assert (m["pushes"], m["voids"]) == (1, 1)


def test_a_played_zero_is_a_miss_and_is_counted(tmp_path):
    """The fixed settlement writes actual 0 / under for a player with snaps and no
    stat row. That is a graded MISS - the case the old settlement dropped."""
    con = store(tmp_path)
    outcome(con, "z", 2.5, "under", 0)
    close(con, "z", 0.5)
    out, census = run(con)
    g = only_game(out)
    assert (g["result"], g["actual"]) == ("missed", 0)
    assert out["00-A"]["main"][0]["career"]["n"] == 1
    assert census["zero_receptions"] == 1


def test_the_latest_settlement_version_wins(tmp_path):
    con = store(tmp_path)
    outcome(con, "z", 2.5, "under", 0)
    con.execute("INSERT INTO outcome_settlement VALUES ('z','v2','over',3,'nflverse',0)")
    close(con, "z", 0.5)
    assert only_game(run(con)[0])["result"] == "cleared"


def test_rates_last_10_this_season_and_career_with_wilson_on_games(tmp_path):
    con = store(tmp_path)
    games = dict(GAMES)
    team = dict(TEAM)
    i = 0
    for season in (2024, 2025):
        for week in range(1, 9):
            gid = f"S{season}W{week}"
            games[gid] = {"game_id": gid, "gameday": None, "home_team": "MIN",
                          "away_team": "CHI", "kickoff_ts": KICK}
            team[("00-A", season, week)] = "MIN"
            res = "over" if season == 2025 else "under"
            outcome(con, f"o{i}", 4.5, res, 5 if res == "over" else 3,
                    season=season, week=week, game=gid)
            close(con, f"o{i}", 0.5)
            i += 1
    out, _ = E.load_main_lines(con, {"00-A"}, games, team, 2025)
    (m,) = out["00-A"]["main"]
    assert m["career"]["n"] == 16 and m["career"]["cleared"] == 8
    assert m["this_season"]["n"] == 8 and m["this_season"]["cleared"] == 8
    assert m["last_10"]["n"] == 10 and m["last_10"]["cleared"] == 8   # 2 from 2024, 8 from 2025
    lo, hi = E.core_stats.wilson(8, 16)
    assert m["career"]["interval"] == [round(lo, 4), round(hi, 4)]
    # a season with nothing in it is n = 0 with a null rate, not a zero
    out, _ = E.load_main_lines(con, {"00-A"}, games, team, 2026)
    assert out["00-A"]["main"][0]["this_season"]["rate"] is None


def test_opponent_and_home_follow_the_players_team_and_are_null_without_it(tmp_path):
    con = store(tmp_path)
    outcome(con, "a", 4.5, "over", 5, week=2, game="G2")
    close(con, "a", 0.5)
    g = only_game(run(con)[0])
    assert (g["opponent"], g["home"], g["date"]) == ("GB", False, "2025-09-14")
    out, _ = E.load_main_lines(con, {"00-A"}, GAMES, {}, 2025)
    g = only_game(out)
    assert (g["opponent"], g["home"]) == (None, None)


def test_attach_gives_every_history_both_fields(tmp_path):
    ph = {"00-A": {"stats": [], "records": []}, "00-B": {"stats": [], "records": []}}
    E.attach_main_lines(ph, {"00-A": {"games": [1], "main": [2]}})
    assert ph["00-A"]["games"] == [1] and ph["00-B"] == {"stats": [], "records": [],
                                                         "games": [], "main": []}


def test_market_keys_used_walks_games_and_main():
    obj = {"kind": "player_summary",
           "prop_history": {"stats": [], "records": [],
                            "games": [{"stat": "planted_game_key"}],
                            "main": [{"stat": "planted_main_key"}]}}
    assert {"planted_game_key", "planted_main_key"} <= E.market_keys_used(obj)
