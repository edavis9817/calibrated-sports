"""S00: the CLV arithmetic, the close definition, and the block bootstrap.

Run: pytest -q tests/test_clv.py

Synthetic. These pin the parts that are easy to get quietly wrong and hard to
notice afterwards: which price belongs to which side, what counts as a close,
and whether an interval respects that 935 rows are not 935 independent facts.
"""
import math
import statistics

import pytest

from research import clv


def row(entry_bid=0.40, entry_ask=0.46, close_bid=0.50, close_ask=0.54,
        side="yes", game="G1", **kw):
    r = {"entry_bid": entry_bid, "entry_ask": entry_ask,
         "close_bid": close_bid, "close_ask": close_ask,
         "entry_mid": (entry_bid + entry_ask) / 2,
         "close_mid": (close_bid + close_ask) / 2,
         "side": side, "sgn": 1.0 if side == "yes" else -1.0,
         "game": game, "entity": "P", "stat": "receptions",
         "buy_yes_entry": None}
    r.update(kw)
    return r


def with_depth(r, ye=0.46, ne=0.60, yc=0.54, nc=0.52):
    """Depth prices are what it COSTS TO BUY each side, so the two sum to more
    than 1 by the effective spread."""
    r.update({"buy_yes_entry": ye, "buy_no_entry": ne,
              "buy_yes_close": yc, "buy_no_close": nc,
              "entry_eff_spread": ye + ne - 1.0,
              "close_eff_spread": yc + nc - 1.0})
    return r


# =============================================================================
# the sign, which is the thing that cost a session
# =============================================================================

def test_yes_gains_when_the_price_rises():
    r = row(side="yes")
    assert clv.clv_mid(r) == pytest.approx(0.09)      # 0.52 - 0.43


def test_no_gains_when_the_price_falls():
    """A no position is long the complement. The same market move that helps
    yes must hurt no by exactly as much."""
    r = row(side="no")
    assert clv.clv_mid(r) == pytest.approx(-0.09)


def test_the_no_side_is_the_complement_not_a_negated_yes_price():
    """1 - close_mid minus 1 - entry_mid. Written out so the identity is
    checked against arithmetic rather than against the implementation."""
    r = row(side="no")
    explicit = (1 - r["close_mid"]) - (1 - r["entry_mid"])
    assert clv.clv_mid(r) == pytest.approx(explicit)


def test_mid_placebo_is_exactly_zero_sum():
    for r in (row(side="yes"), row(side="no"),
              row(entry_bid=0.02, entry_ask=0.09, close_bid=0.01, close_ask=0.03)):
        assert clv.clv_mid(r, "yes") + clv.clv_mid(r, "no") == pytest.approx(0.0, abs=1e-15)


# =============================================================================
# executable: the placebo must NOT be zero-sum, and must be exactly the spread
# =============================================================================

def test_executable_yes_pays_the_offer_and_leaves_at_the_bid():
    r = with_depth(row(side="yes"))
    # buy yes at 0.46; at the close yes can be sold for 1 - 0.52 = 0.48
    assert clv.clv_exec(r) == pytest.approx(0.48 - 0.46)


def test_executable_no_uses_the_no_book_at_both_ends():
    r = with_depth(row(side="no"))
    # buy no at 0.60; at the close no can be sold for 1 - 0.54 = 0.46
    assert clv.clv_exec(r) == pytest.approx(0.46 - 0.60)


def test_executable_placebo_equals_minus_both_effective_spreads():
    """THE REAL EXECUTABLE PLACEBO. Taking both sides pays to cross twice, so
    the sum is not zero - it is exactly the two effective spreads, negated. A
    test demanding zero here would be demanding a free round trip."""
    for ye, ne, yc, nc in ((0.46, 0.60, 0.54, 0.52),
                           (0.10, 0.95, 0.08, 0.96),
                           (0.50, 0.51, 0.49, 0.53)):
        r = with_depth(row(), ye, ne, yc, nc)
        got = clv.clv_exec(r, "yes") + clv.clv_exec(r, "no")
        want = -((ye + ne - 1.0) + (yc + nc - 1.0))
        assert got == pytest.approx(want, abs=1e-12)


def test_executable_is_absent_rather_than_guessed_when_depth_is_missing():
    assert clv.clv_exec(row()) is None


def test_mid_is_absent_when_the_yes_book_is_one_sided():
    """A NULL bid is a real one-sided book. Coercing it to zero at one end and
    reading a real bid at the other would manufacture CLV from a convention
    change rather than from a price move."""
    r = row()
    r["entry_mid"] = None
    assert clv.clv_mid(r) is None


# =============================================================================
# the close is before kickoff, and that is a rule
# =============================================================================

def test_quote_at_uses_a_strict_bound_for_the_close(tmp_path):
    import sqlite3
    p = tmp_path / "q.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE quotes (ts REAL, venue TEXT, market_id TEXT, "
              "best_bid REAL, best_ask REAL)")
    for ts in (100.0, 200.0, 300.0):
        c.execute("INSERT INTO quotes VALUES (?,'kalshi','M',0.4,0.5)", (ts,))
    c.commit()
    # strict: a quote exactly at kickoff is a LIVE price and must be refused
    assert clv.quote_at(c, "M", 300.0, strict=True)[0] == 200.0
    # non-strict at entry: the entry quote may be exactly at the entry instant
    assert clv.quote_at(c, "M", 300.0, strict=False)[0] == 300.0


def test_a_quote_without_an_ask_is_not_a_price(tmp_path):
    import sqlite3
    p = tmp_path / "q2.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE quotes (ts REAL, venue TEXT, market_id TEXT, "
              "best_bid REAL, best_ask REAL)")
    c.execute("INSERT INTO quotes VALUES (100.0,'kalshi','M',0.4,0.5)")
    c.execute("INSERT INTO quotes VALUES (200.0,'kalshi','M',0.4,NULL)")
    c.commit()
    assert clv.quote_at(c, "M", 300.0, strict=True)[0] == 100.0


# =============================================================================
# the interval must respect the blocks
# =============================================================================

def test_bootstrap_blocks_do_not_narrow_when_rows_are_duplicated():
    """Duplicating every row inside its own game adds no information: the same
    game still moved the same way. An interval that shrinks is treating a
    ladder's rungs as independent games."""
    base = [row(game=f"G{i}", close_bid=0.50 + 0.01 * i,
                close_ask=0.54 + 0.01 * i) for i in range(12)]
    dup = [dict(r) for r in base for _ in range(20)]
    a = clv.block_bootstrap(base, clv.clv_mid, "game")
    b = clv.block_bootstrap(dup, clv.clv_mid, "game")
    assert a["blocks"] == b["blocks"] == 12
    assert a["mean"] == pytest.approx(b["mean"])
    wa = a["hi"] - a["lo"]
    wb = b["hi"] - b["lo"]
    assert wb == pytest.approx(wa, rel=0.05), (
        f"interval width moved {wa:.5f} -> {wb:.5f} on duplicated rows")


def test_bootstrap_is_deterministic_for_a_fixed_seed():
    rows = [row(game=f"G{i}", close_bid=0.4 + 0.02 * i, close_ask=0.44 + 0.02 * i)
            for i in range(10)]
    a = clv.block_bootstrap(rows, clv.clv_mid, "game", n=500)
    b = clv.block_bootstrap(rows, clv.clv_mid, "game", n=500)
    assert (a["lo"], a["hi"]) == (b["lo"], b["hi"])


def test_bootstrap_refuses_a_single_block():
    rows = [row(game="G1") for _ in range(50)]
    assert clv.block_bootstrap(rows, clv.clv_mid, "game") is None


def test_bootstrap_recovers_a_known_mean():
    rows = [row(game=f"G{i}", close_bid=0.45, close_ask=0.49) for i in range(30)]
    res = clv.block_bootstrap(rows, clv.clv_mid, "game")
    assert res["mean"] == pytest.approx(0.47 - 0.43)
    assert res["lo"] == pytest.approx(res["hi"])   # zero variance


# =============================================================================
# the side comes from the selection code, not from a copy of it
# =============================================================================

def test_side_is_taken_from_paper_trade_not_reimplemented():
    import inspect
    src = inspect.getsource(clv)
    code = "\n".join(l.split("#", 1)[0] for l in src.splitlines())
    assert "from jobs.paper_trade import evaluate" in code
    assert "evaluate(" in code


def test_evaluate_picks_yes_above_the_mid_and_no_below():
    from jobs.paper_trade import evaluate
    assert evaluate(0.80, 0.40, 0.46)["side"] == "yes"
    assert evaluate(0.10, 0.40, 0.46)["side"] == "no"
