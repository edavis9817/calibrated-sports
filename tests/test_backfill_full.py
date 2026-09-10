"""Full-backfill tests. Run: pytest -q

Guarding a 55k-credit job. Every failure below is one that either spends money
twice or silently destroys what the money bought.
"""
import time

import pytest

import config
import store
from jobs import prune_quotes
from jobs.backfill_oddsapi import (BENCHMARK_BOOKS, OVERROUND_MAX,
                                   OVERROUND_MIN, OverroundViolation,
                                   ReserveExhausted, Spend, devig_pair)


# --- the overround invariant -------------------------------------------------

def test_devig_removes_exactly_the_vig():
    """Hand-checked against the pilot's own row: AJ Brown Over 5.5 at -135 vs
    Under at +105 - 0.5745 and 0.4878, a 6.23% overround."""
    over, under, total = devig_pair(0.5745, 0.4878, "AJ Brown 5.5")
    assert total == pytest.approx(1.0623, abs=1e-4)
    assert over + under == pytest.approx(1.0)
    assert over == pytest.approx(0.5408, abs=1e-4)
    assert over > 0.5 and under < 0.5      # the favourite stays the favourite


def test_a_sum_at_one_means_already_devigged():
    """Not a rounding quibble. De-vigging an already-de-vigged feed applies the
    adjustment twice and the result still looks like a probability."""
    with pytest.raises(OverroundViolation) as e:
        devig_pair(0.50, 0.50, "x")
    assert "already de-vigged" in str(e.value)


def test_a_sum_far_from_one_means_mismatched_sides():
    """Two rows that are not opposite sides of the same claim. This is the one
    that produces plausible numbers and wrong ones."""
    with pytest.raises(OverroundViolation) as e:
        devig_pair(0.90, 0.90, "x")
    assert "mismatched" in str(e.value)
    with pytest.raises(OverroundViolation):
        devig_pair(0.20, 0.20, "x")


def test_the_overround_window_is_a_real_bookmaker_range():
    """1.00-1.15 spans a sharp two-way market to a wide one. Outside it, the
    pair is not a pair."""
    assert OVERROUND_MIN == 1.00 and OVERROUND_MAX == 1.15
    devig_pair(0.53, 0.49, "tight")        # 1.02, fine
    devig_pair(0.58, 0.56, "wide")         # 1.14, still fine
    with pytest.raises(OverroundViolation):
        devig_pair(0.60, 0.56, "too wide")  # 1.16


def test_a_missing_side_devigs_to_nothing_rather_than_guessing():
    assert devig_pair(0.55, None) == (None, None, None)
    assert devig_pair(None, 0.45) == (None, None, None)


# --- the credit floor --------------------------------------------------------

class _Resp:
    def __init__(self, remaining, last=50):
        self.headers = {"x-requests-remaining": str(remaining),
                        "x-requests-last": str(last)}


def test_the_reserve_aborts_rather_than_warning():
    """A budget guard that logs and continues is not a guard; by the time
    anyone reads the warning the credits are gone."""
    s = Spend(reserve=20000)
    s.note(_Resp(20100))                    # above the floor, fine
    assert s.remaining == 20100
    with pytest.raises(ReserveExhausted) as e:
        s.note(_Resp(20000))                # AT the floor, not below
    assert "20,000" in str(e.value) or "20000" in str(e.value)


def test_spend_tracks_real_credits_from_headers():
    s = Spend(reserve=0)
    s.note(_Resp(99_000, last=1))
    s.note(_Resp(98_950, last=50))
    assert s.credits == 51
    assert s.calls == 2
    assert s.remaining == 98_950


def test_no_reserve_means_no_abort():
    s = Spend(reserve=None)
    s.note(_Resp(1))
    assert s.remaining == 1


# --- retention must not eat what the credits bought --------------------------

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setattr(config, "QUOTES_PRUNE_SOURCES", ("live",))
    store.init_db()
    store.reset_quote_state()
    yield tmp_path
    store.reset_quote_state()


def _q(ts, source, market_id="M"):
    return {"ts": ts, "sport": "nfl", "venue": "oddsapi:dk", "market_id": market_id,
            "mid": 0.5, "source": source}


def _age(market_id, days):
    """Backdate a row's INGESTION time. write_quotes stamps ingest_ts from the
    wall clock and takes no argument for it, which is the point - so a test
    that wants an old row has to say so out loud."""
    with store.db() as c:
        c.execute("UPDATE quotes SET ingest_ts=? WHERE market_id=?",
                  (time.time() - days * 86400, market_id))


def test_retention_never_prunes_a_paid_backfill(env):
    """THE regression. Backfilled rows carry the timestamp of the EVENT, not of
    the fetch, so a 14-day window on `ts` deletes a two-year-old historical row
    the moment it lands. This silently destroyed brief 009's entire 806-credit
    pilot and 76 days of brief 006's Kalshi candles."""
    ancient = time.time() - 700 * 86400
    store.write_quotes([_q(ancient, "oddsapi_historical", "hist"),
                        _q(ancient, "backfill:kalshi_candles", "kal"),
                        _q(ancient, "live", "old_live"),
                        _q(time.time(), "live", "new_live")], dedupe=False)
    _age("old_live", 30)                    # ingested a month ago, not now

    stats = prune_quotes.run(days=14)

    assert stats["deleted"] == 1            # only the stale LIVE row
    assert stats["protected"] == 2          # and it says so
    with store.db() as c:
        left = {r[0] for r in c.execute("SELECT market_id FROM quotes")}
    assert left == {"hist", "kal", "new_live"}


def test_prune_still_bounds_live_growth(env):
    """The fix must not turn retention off - live capture is what grows."""
    old = time.time() - 30 * 86400
    store.write_quotes([_q(old, "live", f"m{i}") for i in range(5)],
                       dedupe=False)
    for i in range(5):
        _age(f"m{i}", 30)
    assert prune_quotes.run(days=14)["deleted"] == 5


# --- rule 2: the age of a row is its INGESTION time, never its event time ----

def test_a_time_based_policy_keys_on_ingestion_not_event_time(env):
    """The rule, stated as a test. A live quote restored from the raw archive
    carries its original event `ts` - two months old - but it was written to
    this database a second ago. Keyed on `ts` it is deleted again on the next
    prune, and the restore is a treadmill. Keyed on `ingest_ts` it survives."""
    two_months_ago = time.time() - 60 * 86400
    store.write_quotes([_q(two_months_ago, "live", "restored")], dedupe=False)

    stats = prune_quotes.run(days=14)

    assert stats["deleted"] == 0
    assert stats["saved_by_ingest_ts"] == 1,         "the row a ts-keyed window would have destroyed"
    with store.db() as c:
        assert c.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == 1


def test_ingest_ts_is_stamped_by_the_writer_not_the_caller(env):
    """A row that could name its own ingestion time could name one in the past,
    and retention would believe it. The column is the one timestamp no upstream
    feed - and no caller - controls."""
    row = _q(time.time() - 500 * 86400, "live", "spoof")
    row["ingest_ts"] = 1.0                  # a caller trying to backdate itself
    store.write_quotes([row], dedupe=False)
    with store.db() as c:
        ts, ing = c.execute(
            "SELECT ts, ingest_ts FROM quotes WHERE market_id='spoof'"
        ).fetchone()
    assert ing > time.time() - 60           # now, not 1.0 and not `ts`
    assert ing - ts > 400 * 86400           # and nothing like the event time


def test_rows_predating_the_column_still_prune(env):
    """A live row written before ingest_ts existed has NULL there. It must not
    become immortal - COALESCE falls back to `ts`, which for live capture is
    the same instant anyway."""
    old = time.time() - 30 * 86400
    store.write_quotes([_q(old, "live", "legacy")], dedupe=False)
    with store.db() as c:
        c.execute("UPDATE quotes SET ingest_ts=NULL WHERE market_id='legacy'")
    assert prune_quotes.run(days=14)["deleted"] == 1


# --- the benchmark -----------------------------------------------------------

def test_the_benchmark_excludes_the_widest_books():
    """bovada and betonlineag stay in the data but out of the consensus: they
    are the widest of the seven and would drag a median meant to represent
    where the sharp money sits. Pinnacle is absent from historical us props."""
    assert set(BENCHMARK_BOOKS) == {"draftkings", "fanduel", "betmgm"}
    assert "bovada" not in BENCHMARK_BOOKS
    assert "betonlineag" not in BENCHMARK_BOOKS


def test_benchmark_records_dispersion_as_a_confidence_signal(env):
    from jobs.backfill_oddsapi import _write_benchmarks
    _write_benchmarks({"oid1": [("draftkings", 0.52), ("fanduel", 0.55),
                                ("betmgm", 0.54)]}, 1000.0)
    with store.db() as c:
        row = c.execute(
            "SELECT n_books, median_devig, min_devig, max_devig, dispersion, "
            "books FROM outcome_benchmark WHERE outcome_id='oid1'").fetchone()
    assert row[0] == 3
    assert row[1] == pytest.approx(0.54)          # median, not mean
    assert (row[2], row[3]) == (pytest.approx(0.52), pytest.approx(0.55))
    assert row[4] == pytest.approx(0.03)
    assert row[5] == "betmgm,draftkings,fanduel"


def test_a_single_book_still_benchmarks_but_with_zero_dispersion(env):
    from jobs.backfill_oddsapi import _write_benchmarks
    _write_benchmarks({"oid2": [("fanduel", 0.61)]}, 1000.0)
    with store.db() as c:
        row = c.execute("SELECT n_books, median_devig, dispersion FROM "
                        "outcome_benchmark WHERE outcome_id='oid2'").fetchone()
    assert row == (1, pytest.approx(0.61), pytest.approx(0.0))


# --- the outcome key must not collapse two different games -------------------

def test_a_january_game_resolves_to_its_own_season(env):
    """Season N week 18 kicks off in January of year N+1. Matching an event to
    a game on the calendar year of commence_time sent every January game to the
    FOLLOWING season's identical matchup: MIN@DET on 2024-01-07 (season 2023)
    was stamped 2024_18_MIN_DET, a game played 364 days later, and its props
    inherited that game's week."""
    from jobs.backfill_oddsapi import match_game
    meta = {("MIN", "DET"): [
        (1704645600.0, "2023_18_MIN_DET", 2023, 18, ("MIN", "DET")),   # Jan 2024
        (1736127600.0, "2024_18_MIN_DET", 2024, 18, ("MIN", "DET")),   # Jan 2025
    ]}
    assert match_game(meta, "MIN", "DET", "2024-01-07T18:00:00Z")[0] == \
        "2023_18_MIN_DET"
    assert match_game(meta, "MIN", "DET", "2025-01-05T18:00:00Z")[0] == \
        "2024_18_MIN_DET"


def test_an_event_with_no_game_within_the_window_is_dropped(env):
    """Better an unmapped market with a reason than a market silently attached
    to the wrong game."""
    from jobs.backfill_oddsapi import match_game
    meta = {("MIN", "DET"): [(1704645600.0, "2023_18_MIN_DET", 2023, 18,
                              ("MIN", "DET"))]}
    assert match_game(meta, "MIN", "DET", "2023-09-10T17:00:00Z") is None
    assert match_game(meta, "GB", "CHI", "2024-01-07T18:00:00Z") is None
    assert match_game(meta, "MIN", "DET", None) is None


def test_featured_stamps_week_per_event_not_per_payload(env):
    """A historical featured snapshot carries every event the API had open at
    that instant. One week for the whole payload collapsed a season of "DAL
    moneyline" claims onto one outcome_id, because the key is (season, week,
    team, side) and nothing else."""
    from jobs.backfill_oddsapi import normalize_featured

    def ev(away, home, price_a, price_h):
        return {"away_team": away, "home_team": home,
                "bookmakers": [{"key": "draftkings", "markets": [
                    {"key": "h2h", "outcomes": [
                        {"name": away, "price": price_a},
                        {"name": home, "price": price_h}]}]}]}

    body = {"timestamp": "2025-09-04T23:50:00Z",
            "data": [ev("Dallas Cowboys", "Philadelphia Eagles", 320, -400),
                     ev("Dallas Cowboys", "New York Giants", -155, 130)]}
    gbp = {("DAL", "PHI"): ("2025_01_DAL_PHI", 2025, 1),
           ("DAL", "NYG"): ("2025_18_DAL_NYG", 2025, 18)}
    rows = normalize_featured(body, 2025, 18, gbp, {}, [])

    dal = {r["market_id"]: r["_outcome_id"] for r in rows
           if r["subject"] == "DAL"}
    assert len(dal) == 2
    assert len(set(dal.values())) == 2, "two DAL games, two outcome_ids"
    with store.db() as c:
        keys = {k for (k,) in c.execute(
            "SELECT key FROM outcomes WHERE entity_id='DAL'")}
    assert keys == {"nfl|2025|wk1|moneyline|dal|na|na|yes",
                    "nfl|2025|wk18|moneyline|dal|na|na|yes"}


def test_featured_still_accepts_the_bare_game_id_form(env):
    """The old mapping shape must keep working; the scalar week is the
    fallback, not the rule."""
    from jobs.backfill_oddsapi import normalize_featured
    body = {"timestamp": "2025-09-04T23:50:00Z",
            "data": [{"away_team": "Dallas Cowboys",
                      "home_team": "Philadelphia Eagles",
                      "bookmakers": [{"key": "fanduel", "markets": [
                          {"key": "h2h", "outcomes": [
                              {"name": "Dallas Cowboys", "price": 320},
                              {"name": "Philadelphia Eagles", "price": -400}]}]}]}]}
    rows = normalize_featured(body, 2025, 1, {("DAL", "PHI"): "2025_01_DAL_PHI"},
                              {}, [])
    assert rows and all(r["event_id"] == "2025_01_DAL_PHI" for r in rows)


def test_a_repeated_payload_replaces_rather_than_doubles(env):
    """The archive holds two payloads for 2024 week 8 - the pilot bought it and
    the full backfill bought it again. Parsing both must leave one copy. This
    is the 578,708 -> 857,676 bug arriving by a different door, and it is
    invisible until someone counts."""
    from jobs.backfill_oddsapi import _replace_and_write
    rows = [{"ts": 1.0, "sport": "nfl", "venue": "oddsapi:draftkings",
             "event_id": "2024_08_X_Y", "market_id": "m1", "mid": 0.5,
             "raw_ref": "oddsapi_historical/event_odds",
             "source": "oddsapi_historical", "_outcome_id": "oid"}]

    _replace_and_write(rows, ["2024_08_X_Y"], "props", True)   # first parse
    _replace_and_write(rows, ["2024_08_X_Y"], "props", False)  # the duplicate

    with store.db() as c:
        assert c.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == 1


def test_a_second_payload_never_deletes_the_first_ones_extra_markets(env):
    """The pilot bought 2024 week 8 WITH `player_receptions_alternate`; the
    full backfill bought the same games without it. Deleting by event before
    parsing the second payload threw the alternate ladder away - paid data,
    lost to a payload that never carried it. Row-level idempotence keeps both
    and still writes each row once."""
    from jobs.backfill_oddsapi import _replace_and_write

    def row(market_id, ts=1.0):
        return {"ts": ts, "sport": "nfl", "venue": "oddsapi:fanduel",
                "event_id": "2024_08_MIN_LA", "market_id": market_id,
                "mid": 0.5, "raw_ref": "oddsapi_historical/event_odds",
                "source": "oddsapi_historical", "_outcome_id": "o_" + market_id}

    seen = set()
    pilot = [row("receptions|5.5"), row("receptions_alternate|2.5")]
    full = [row("receptions|5.5"), row("reception_yds|40.5")]
    _replace_and_write(pilot, ["2024_08_MIN_LA"], "props", True, seen)
    _replace_and_write(full, ["2024_08_MIN_LA"], "props", True, seen)

    with store.db() as c:
        got = [r[0] for r in c.execute("SELECT market_id FROM quotes")]
    assert len(got) == 3, "each row written once"
    assert set(got) == {"receptions|5.5", "receptions_alternate|2.5",
                        "reception_yds|40.5"}
