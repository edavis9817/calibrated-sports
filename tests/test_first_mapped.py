"""The two things that hid a four-week mapping outage (a-73).

1. AN UNEXAMINED MARKET HAD NO REPRESENTATION. `market_outcome.unmapped_reason`
   exists only on a row, and a market the mapper has not looked at has no row,
   so every census of reasons read clean while 94% of a slate was unmapped.
   `map_markets.state_census` reads from `markets` and has a third state.

2. `market_outcome.mapped_ts` CANNOT SAY WHEN A MARKET WAS FIRST MAPPED. Every
   full pass rewrites it. `outcomes.created_ts` is written once, and for a
   Kalshi player prop - where Kalshi is the venue that creates the outcome -
   it is the first-mapped time. `analytics.staleness.check_mapped_rate` and
   `research/mapping_lead.py` rely on exactly that, so it is pinned here.

Every test runs on the store `tests.test_map_pending.env` builds under
tmp_path; none opens a configured one.
"""
import sqlite3
import time

import pytest

import config
import store
from jobs import map_markets
from tests.test_map_pending import (EID, GSIS, K7, KICK, _book_slate, env,  # noqa: F401
                                    kalshi_rung, mo)

NOBODY = "KXNFLREC-26SEP09NESEA-SEANOBODY1-5"


def created(outcome_id):
    with store.db() as c:
        return c.execute("SELECT created_ts FROM outcomes WHERE outcome_id=?",
                         (outcome_id,)).fetchone()[0]


def states(venue="kalshi"):
    return {r["market_type"]: (r["listed"], r["mapped"], r["refused"], r["unexamined"])
            for r in map_markets.state_census() if r["venue"] == venue}


# --- first mapped ---------------------------------------------------------------

def test_a_full_pass_rewrites_mapped_ts_and_leaves_created_ts_alone():
    """Both halves: the column that cannot be used moves, the one relied on
    does not. Without the first assertion the second proves nothing about why."""
    store.upsert_markets([kalshi_rung()])
    map_markets.run_pending(book_props=False)
    oid, _, _, first_mapped_ts = mo("kalshi", K7)
    first_created = created(oid)
    time.sleep(0.05)
    map_markets.run("kalshi")                      # the Tuesday pass
    again = mo("kalshi", K7)
    assert again[0] == oid
    assert again[3] > first_mapped_ts              # mapped_ts now says "Tuesday"
    assert created(oid) == first_created           # created_ts still says when


@pytest.mark.parametrize("writer", ["upsert_outcome", "upsert_outcomes"])
def test_neither_outcome_writer_moves_created_ts_on_a_second_write(writer):
    """There are TWO writers - Kalshi's mapper calls the single form, the book
    join and the backfill the batch - and a test through the mapper alone
    reaches one of them. Each is written twice here, directly."""
    from core.outcomes import Stat, player_prop
    o = player_prop(2026, 1, GSIS, Stat.RECEPTIONS, 6.5)

    def write():
        if writer == "upsert_outcome":
            store.upsert_outcome(o, "2026_01_NE_SEA")
        else:
            store.upsert_outcomes([(o, "2026_01_NE_SEA")])
    write()
    first = created(o.outcome_id)
    time.sleep(0.05)
    write()
    assert created(o.outcome_id) == first


def test_created_ts_is_the_time_of_the_first_pass_not_of_the_listing():
    store.upsert_markets([kalshi_rung()])
    listed = time.time()
    time.sleep(0.05)
    map_markets.run_pending(book_props=False)
    assert listed < created(mo("kalshi", K7)[0]) <= time.time()


def test_a_book_line_links_to_the_kalshi_outcome_and_does_not_move_its_created_ts():
    """Why this is a Kalshi-prop fact only: the book's market shares the
    outcome, so the outcome's created_ts is KALSHI's first-mapped time and
    says nothing about when the book line was linked."""
    store.upsert_markets([kalshi_rung()])
    map_markets.run_pending(book_props=False)
    oid = mo("kalshi", K7)[0]
    first = created(oid)
    _book_slate()
    time.sleep(0.05)
    res = map_markets.run_pending(now=KICK - 3600)
    assert res["stats"]["oddsapi_props:newly_linked"] == 1
    with store.db() as c:
        linked = c.execute("SELECT COUNT(*) FROM market_outcome WHERE outcome_id=? "
                           "AND venue LIKE 'oddsapi:%'", (oid,)).fetchone()[0]
    assert linked == 1 and created(oid) == first


# --- three states ---------------------------------------------------------------

def test_the_census_has_a_third_state_and_a_market_is_in_exactly_one():
    store.upsert_markets([kalshi_rung(7), kalshi_rung(5, ticker=NOBODY, name="No Body")])
    assert states() == {"prop": (2, 0, 0, 2)}             # listed, nobody has looked
    map_markets.run_pending(book_props=False)
    assert states() == {"prop": (2, 1, 1, 0)}             # one mapped, one REFUSED
    store.upsert_markets([kalshi_rung(4)])                # Thursday 17:00Z
    assert states() == {"prop": (3, 1, 1, 1)}
    for listed, mapped, refused, unexamined in states().values():
        assert listed == mapped + refused + unexamined


def test_the_reason_census_alone_cannot_see_an_unexamined_market():
    """The defect, stated as a test: grouping market_outcome by reason returns
    nothing for a slate nobody has looked at."""
    store.upsert_markets([kalshi_rung(n) for n in (3, 4, 5, 6, 7)])
    with store.db() as c:
        by_reason = c.execute("SELECT unmapped_reason, COUNT(*) FROM market_outcome "
                              "WHERE outcome_id IS NULL GROUP BY 1").fetchall()
    assert by_reason == []
    assert states()["prop"][3] == 5


def test_unmapped_prints_the_unexamined_before_any_reason(capsys):
    store.upsert_markets([kalshi_rung(7), kalshi_rung(5, ticker=NOBODY, name="No Body")])
    map_markets.run_pending(book_props=False)
    store.upsert_markets([kalshi_rung(4), kalshi_rung(3)])
    map_markets.unmapped(5)
    out = capsys.readouterr().out
    assert "unexamined (no market_outcome row, so in NO reason census): 2" in out
    assert "     2  %s" % map_markets.UNEXAMINED in out
    assert "no player matches 'No Body'" in out
    assert out.index("unexamined (no") < out.index("unmapped by reason:") \
        < out.index(map_markets.UNEXAMINED) < out.index("no player matches")


def test_unmapped_prints_a_zero_when_everything_has_been_looked_at(capsys):
    store.upsert_markets([kalshi_rung(7)])
    map_markets.run_pending(book_props=False)
    map_markets.unmapped(5)
    out = capsys.readouterr().out
    assert "unexamined (no market_outcome row, so in NO reason census): 0" in out
    assert map_markets.UNEXAMINED not in out


def test_coverage_carries_the_unexamined_column(capsys):
    store.upsert_markets([kalshi_rung(7), kalshi_rung(4)])
    map_markets.run_pending(book_props=False)
    store.upsert_markets([kalshi_rung(3)])
    map_markets.coverage()
    out = capsys.readouterr().out
    head = [l for l in out.splitlines() if l.startswith("venue")][0].split()
    row = [l for l in out.splitlines() if l.startswith("kalshi") and " prop " in l][0].split()
    assert head[-2:] == ["refused", "unexamined"]
    assert row[2:4] == ["3", "2"] and row[-2:] == ["0", "1"]


# --- the pass leaves nothing unexamined -----------------------------------------

def test_the_pending_pass_takes_every_venue_a_mapper_exists_for():
    """`unexamined` must mean "the pass has not run", never "this venue is not
    on the timer": a venue left out would hold the gate's unexamined item red
    on a healthy logger, which is how a guard gets switched off."""
    assert set(map_markets.PENDING_VENUES) == set(map_markets.MAPPERS)
    assert map_markets.PENDING_VENUES[0] == "kalshi"      # links need its outcomes first


def test_one_pass_leaves_no_market_unexamined_and_says_so():
    _book_slate()                                          # an `oddsapi` game event
    store.upsert_markets([kalshi_rung(7), kalshi_rung(5, ticker=NOBODY, name="No Body"),
                          {"venue": "polymarket", "market_id": "0xfut", "market_type": "future",
                           "title": "Super Bowl winner"}])
    before = sum(r["unexamined"] for r in map_markets.state_census())
    res = map_markets.run_pending(now=KICK - 3600)
    assert before == 4
    assert res["stats"]["unexamined_left"] == 0
    assert sum(r["unexamined"] for r in map_markets.state_census()) == 0
    # the book game event was LOOKED AT and refused, not skipped
    assert mo("oddsapi", f"game:{EID}")[0] is None and mo("oddsapi", f"game:{EID}")[2]
    with store.db() as c:
        detail = c.execute("SELECT detail FROM source_health WHERE source='mapping_pending'").fetchone()[0]
    assert "unexamined_left=0" in detail


# --- the reports are read-only ---------------------------------------------------

@pytest.mark.parametrize("flag", ["--coverage", "--unmapped"])
def test_a_report_flag_never_opens_the_store_for_writing(flag, monkeypatch, capsys):
    """`--coverage` ran `store.init_db()` first: a report-only flag that opened
    the live store read-write (relay ledger, 2026-09-22)."""
    store.upsert_markets([kalshi_rung(7)])

    def refuse(*a, **k):
        raise AssertionError("a report opened the store for writing")
    monkeypatch.setattr(store, "init_db", refuse)
    monkeypatch.setattr(store, "db", refuse)
    monkeypatch.setattr("sys.argv", ["map_markets", flag])
    map_markets.main()
    assert "unexamined (no market_outcome row" in capsys.readouterr().out


def test_the_mapping_flags_still_initialise_the_store(monkeypatch):
    """The other half: moving init_db below the reports must not have removed
    it from the paths that write."""
    called = []
    real = store.init_db
    monkeypatch.setattr(store, "init_db", lambda *a, **k: (called.append(1), real(*a, **k))[1])
    monkeypatch.setattr("sys.argv", ["map_markets", "--pending", "--venue", "kalshi"])
    map_markets.main()
    assert called == [1]


def test_state_census_reads_a_store_it_is_handed_read_only(tmp_path):
    store.upsert_markets([kalshi_rung(7)])
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    try:
        rows = map_markets.state_census(con, now=time.time() + 3600, older_than=1800)
        with pytest.raises(sqlite3.OperationalError):
            con.execute("DELETE FROM markets")
    finally:
        con.close()
    assert [(r["venue"], r["unexamined"], r["stale"]) for r in rows] == [("kalshi", 1, 1)]
    assert map_markets.state_census(now=time.time(), older_than=1800)[0]["stale"] == 0
