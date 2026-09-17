"""Prop history: the settled record, counted once. Run: pytest -q tests/test_prop_history.py

WHAT THIS PUBLISHES AND WHAT IT DOES NOT. A hit-rate record is a FACT and is
publishable under the editorial line - "11-6 to the over" is research. It is not
a pick, it carries no forecast, and it needs no closing price: `outcomes.line`
and `outcome_settlement.result` are sufficient, and `line` is non-null on 100% of
settled rows in every season (measured 2026-09-17). That is why 2026 appears here
at all, while `outcome_close` has no 2026 rows whatsoever.

EVERY COUNT GOES THROUGH `core.stats.hit_rate`. A settled prop exists twice in
`outcomes` - once as the over, once as the under - carrying the SAME market-level
result, so counting both doubles `n` and hands the page an interval sqrt(2) too
narrow. The rule lives in `core.stats` and is not re-implemented here; these
tests assert that this module OBEYS it rather than owning a second copy of it.

ORDERING IS DATA, NOT PROSE. The page leads with the priority markets and does
not suppress receiving yards, which has by far the deepest record. Under the W07
claims rule a sentence like "yards has the most data" is a comparative claim and
cannot be a typed string, so this emits `priority` per stat and the full `n`, and
the site does the wording.
"""
import sqlite3

import pytest

from core import stats as S
from jobs import export_web as E


# --------------------------------------------------------------------- fixture

def store(tmp_path, rows):
    """A store with just the two tables this reads. `rows` are
    (season, week, gsis, stat, line, side, result)."""
    p = tmp_path / "m.db"
    con = sqlite3.connect(p)
    con.executescript("""
        CREATE TABLE outcomes (outcome_id TEXT, key TEXT, sport TEXT, season INT,
            week INT, entity_type TEXT, entity_id TEXT, stat TEXT, line REAL,
            side TEXT, push_possible INT, event_id TEXT, created_ts REAL);
        CREATE TABLE outcome_settlement (outcome_id TEXT, data_version TEXT,
            result TEXT, actual REAL, source TEXT, settled_ts REAL, void_reason TEXT);
    """)
    for i, (season, week, gsis, stat, line, side, result) in enumerate(rows):
        oid = f"o{i}"
        con.execute("INSERT INTO outcomes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (oid, oid, "nfl", season, week, "player", gsis, stat, line,
                     side, 0, "e", 0.0))
        con.execute("INSERT INTO outcome_settlement VALUES (?,?,?,?,?,?,?)",
                    (oid, "v1", result, 0.0, "test", 0.0, None))
    con.commit()
    return con


def both_sides(season, week, gsis, stat, line, result):
    """The real shape: one market stored twice, same result on both rows."""
    return [(season, week, gsis, stat, line, "over", result),
            (season, week, gsis, stat, line, "under", result)]


# ------------------------------------------------------- counted once, not twice

def test_a_market_stored_on_both_sides_counts_ONCE(tmp_path):
    con = store(tmp_path, both_sides(2025, 1, "00-A", "receptions", 4.5, "over"))
    h = E.load_prop_history(con, {"00-A"})["00-A"]
    rec = h["stats"][0]
    assert rec["n"] == 1, "both sides of one market were counted"
    assert rec["cleared"] == 1


def test_the_rate_is_unchanged_but_n_is_not(tmp_path):
    """The defect's signature: pooling leaves the rate alone and doubles n. If
    this module ever regressed, `rate` would look right and only `n` would lie -
    which is exactly why it is asserted."""
    rows = []
    for wk in range(1, 11):
        rows += both_sides(2025, wk, "00-A", "receptions", 4.5,
                           "over" if wk <= 4 else "under")
    h = E.load_prop_history(store(tmp_path, rows), {"00-A"})["00-A"]
    rec = h["stats"][0]
    assert rec["n"] == 10, f"n={rec['n']}, expected 10 markets not 20 rows"
    assert rec["cleared"] == 4
    assert rec["rate"] == pytest.approx(0.4)


def test_a_ONE_SIDED_market_is_kept(tmp_path):
    """4,211 markets are one-sided; anytime_td has no under rows at all. A
    `side == 'over'` filter would drop them silently."""
    con = store(tmp_path, [
        (2026, 1, "00-A", "anytime_td", 0.5, "over", "over"),
        (2026, 2, "00-A", "receptions", 3.5, "under", "under"),
    ])
    h = E.load_prop_history(con, {"00-A"})["00-A"]
    assert sum(s["n"] for s in h["stats"]) == 2


def test_it_uses_the_shared_guard_rather_than_its_own_rule(tmp_path):
    """The rule has ONE home. Asserted by behaviour: a population the guard
    rejects must not be silently summarised here."""
    rows = both_sides(2025, 1, "00-A", "receptions", 4.5, "over")
    raw = [{"season": 2025, "week": 1, "entity_id": "00-A", "stat": "receptions",
            "line": 4.5, "side": s, "result": "over"} for s in ("over", "under")]
    with pytest.raises(S.PooledSides):
        S.hit_rate(raw)                      # the guard rejects the raw shape
    E.load_prop_history(store(tmp_path, rows), {"00-A"})   # the loader must not


# ------------------------------------------------------------- what is emitted

def test_priority_markets_lead_and_yards_is_not_suppressed(tmp_path):
    """Ethan, 2026-09-17: lead with receptions and rush attempts, do not
    suppress yards - hiding the deepest data to flatter the model's own scope
    would be its own dishonesty. Ordering is DATA; the wording is the site's."""
    rows = []
    for wk in range(1, 21):
        rows += both_sides(2025, wk, "00-A", "receiving_yards", 40.5, "over")
    rows += both_sides(2025, 1, "00-A", "receptions", 4.5, "over")
    rows += both_sides(2025, 1, "00-A", "rush_attempts", 2.5, "under")
    h = E.load_prop_history(store(tmp_path, rows), {"00-A"})["00-A"]

    order = [s["stat"] for s in h["stats"]]
    assert order[:2] == ["receptions", "rush_attempts"], order
    assert "receiving_yards" in order, "yards must not be suppressed"
    yards = next(s for s in h["stats"] if s["stat"] == "receiving_yards")
    assert yards["n"] == 20, "yards keeps its full record - the site says it is deepest"
    assert [s["priority"] for s in h["stats"]][:2] == [True, True]
    assert yards["priority"] is False


def test_every_stat_carries_its_interval_and_its_n(tmp_path):
    """No estimate without its interval and its sample count."""
    rows = []
    for wk in range(1, 18):
        rows += both_sides(2025, wk, "00-A", "receptions", 4.5,
                           "over" if wk < 8 else "under")
    h = E.load_prop_history(store(tmp_path, rows), {"00-A"})["00-A"]
    rec = h["stats"][0]
    assert rec["n"] == 17 and rec["cleared"] == 7
    lo, hi = rec["interval"]
    assert lo < rec["rate"] < hi and 0.0 <= lo and hi <= 1.0


def test_records_are_at_the_season_stat_LINE_grain(tmp_path):
    """"11-6 at 4.5 receptions" is a record; averaged across lines it is not.
    A ladder prices several lines in one game and they are different claims."""
    rows = (both_sides(2025, 1, "00-A", "receptions", 3.5, "over")
            + both_sides(2025, 1, "00-A", "receptions", 5.5, "under"))
    h = E.load_prop_history(store(tmp_path, rows), {"00-A"})["00-A"]
    lines = sorted(r["line"] for r in h["records"])
    assert lines == [3.5, 5.5], "two lines in one game collapsed into one record"


def test_a_player_outside_scope_is_absent(tmp_path):
    """Only players with a page. 681 players with settled props have none, and
    inventing entries for them would publish keys nothing links to."""
    con = store(tmp_path, both_sides(2025, 1, "00-B", "sacks", 0.5, "over"))
    assert E.load_prop_history(con, {"00-A"}) == {}


def test_a_player_with_no_props_gets_nothing(tmp_path):
    con = store(tmp_path, [])
    assert E.load_prop_history(con, {"00-A"}) == {}


def test_void_and_unsettled_rows_are_excluded(tmp_path):
    """Only over/under settle. A void prop is not a miss."""
    con = store(tmp_path, [
        (2026, 1, "00-A", "receptions", 4.5, "over", "void"),
        (2026, 2, "00-A", "receptions", 4.5, "over", "unsettled"),
        (2026, 3, "00-A", "receptions", 4.5, "over", "over"),
    ])
    h = E.load_prop_history(con, {"00-A"})["00-A"]
    assert h["stats"][0]["n"] == 1
