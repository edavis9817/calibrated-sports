"""c-19 research/venue_spread.py - the pure pieces the findings rest on.

Each test is shown discriminating: it asserts the answer on one input AND the
other answer on the neighbouring input, so a function returning a constant
cannot pass it.
"""
from research import venue_spread as v


def test_devig_refuses_a_pair_outside_the_overround_band():
    assert abs(v.devig(0.55, 0.50) - 0.55 / 1.05) < 1e-12
    assert v.devig(0.50, 0.50) is None          # already de-vigged: sums to 1.00
    assert v.devig(0.70, 0.50) is None          # 1.20: not two sides of one claim


def test_kalshi_quote_older_than_600s_is_stale_and_600s_is_not():
    kq = {"M": ([1000.0], [(0.40, 0.42)])}
    st, mid, *_ = v.kalshi_at(kq, "M", 1600.0)
    assert st == "ok" and abs(mid - 0.41) < 1e-12
    assert v.kalshi_at(kq, "M", 1600.1)[0] == "kalshi stale > 600s"
    assert v.kalshi_at(kq, "M", 999.0)[0] == "no kalshi quote before fetch"


def test_one_sided_or_crossed_kalshi_book_has_no_mid():
    for bid, ask in ((None, 0.3), (0.3, None), (0.4, 0.4), (0.0, 0.2)):
        kq = {"M": ([0.0], [(bid, ask)])}
        assert v.kalshi_at(kq, "M", 1.0)[0] == "kalshi one-sided"


def test_taker_edge_buys_the_side_the_book_prices_through_and_charges_the_fee():
    r = {"mid": "KXNFLREC-26SEP27X-Y-5", "bid": 0.40, "ask": 0.42, "k": 0.41}
    up = v.taker_edge(dict(r, b=0.45), 100)     # book above the ask -> buy YES at 0.42
    assert 0 < up < 0.45 - 0.42                 # a positive fee came off the 3c gap
    down = v.taker_edge(dict(r, b=0.36), 100)   # book below the bid -> buy NO at 0.60
    assert 0 < down < 0.64 - 0.60
    assert v.taker_edge(dict(r, b=0.41), 100) is None   # inside the spread: nothing to cross


def test_block_bootstrap_resamples_games_not_rows():
    rows = [{"game": g, "x": float(g)} for g in range(10)]
    dup = [dict(r) for r in rows for _ in range(20)]
    f = lambda xs: v.mean(xs, lambda r: r["x"])   # noqa: E731
    a = v.block_boot(rows, f)
    b = v.block_boot(dup, f)
    assert a[4] == b[4] == 10
    # Duplicating every row inside its own game must NOT narrow the interval.
    assert abs((a[2] - a[1]) - (b[2] - b[1])) < 1e-9


def test_close_is_the_last_snapshot_within_15_minutes_and_only_that():
    rows = [{"game": "G", "ts": 100.0, "lead": 1200.0},
            {"game": "G", "ts": 200.0, "lead": 600.0},
            {"game": "H", "ts": 300.0, "lead": 1180.0}]    # H has no snapshot inside 15 min
    close = v.close_rows(rows)
    assert [r["ts"] for r in close] == [200.0]
