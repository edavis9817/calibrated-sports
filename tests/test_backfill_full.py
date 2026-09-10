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
    assert prune_quotes.run(days=14)["deleted"] == 5


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
