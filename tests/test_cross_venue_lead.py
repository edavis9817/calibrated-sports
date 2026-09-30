"""a-59: which venue moves first. Every check here is shown giving BOTH answers."""
import sqlite3

from research import cross_venue_lead as X
from research import inactives as I

KICK = 900.0          # every synthetic row below is after kickoff: the live tier, 10s polls


def rows(path):
    """[(ts, bid, ask, mid, source, source_ts, side)] from [(ts, mid, spread)]."""
    return [(t, round(m - s / 2, 4), round(m + s / 2, 4), m, "live", None, None) for t, m, s in path]


def flat_then_jump(t_jump, lo, hi, spread=0.02, start=500, end=2500, step=10):
    return rows([(t, lo if t < t_jump else hi, spread) for t in range(start, end, step)])


def claim(k_rows, p_rows):
    o = {"id": "o1", "event_id": "g1", "key": "nfl|2026|wk2|total|g|na|44.5|over", "kick": KICK,
         "joined": "store", "venues": {"kalshi": [("kalshi", "K")], "polymarket": [("polymarket", "P")]}}
    return o, {("kalshi", "K"): k_rows, ("polymarket", "P"): p_rows}


def one(k_rows, p_rows):
    o, q = claim(k_rows, p_rows)
    eps, _ = X.polled_episodes(o, q, {}, 0.05)
    assert len(eps) == 1, eps
    return eps[0]


def test_the_venue_that_jumps_first_is_first_and_the_lead_is_signed():
    e = one(flat_then_jump(1000, 0.40, 0.50), flat_then_jump(1060, 0.40, 0.50))
    assert e["outcome"] == "kalshi first" and e["lead_kalshi_s"] == 60
    e = one(flat_then_jump(1060, 0.40, 0.50), flat_then_jump(1000, 0.40, 0.50))
    assert e["outcome"] == "polymarket first" and e["lead_kalshi_s"] == -60


def test_a_gap_no_wider_than_the_poll_is_a_tie_not_a_lead():
    e = one(flat_then_jump(1000, 0.40, 0.50), flat_then_jump(1010, 0.40, 0.50))
    assert e["outcome"] == "tie within resolution"


def test_a_jump_on_a_wide_book_is_not_a_move():
    # Polymarket's mid jumps the same 10pp at the same instant, on a 20c book:
    # that is the midpoint of an absence, so Kalshi's move is "alone"
    e = one(flat_then_jump(1000, 0.40, 0.50), flat_then_jump(1000, 0.40, 0.50, spread=0.20))
    assert e["outcome"] == "kalshi alone"
    # and the diagnostic sees that the other mid did travel
    assert e["other_drifted"] is True


def test_a_move_across_a_logger_gap_is_not_timed():
    k = [r for r in flat_then_jump(1000, 0.40, 0.50) if not 600 < r[0] < 1000]
    o, q = claim(k, flat_then_jump(1500, 0.40, 0.50))
    eps, notes = X.polled_episodes(o, q, {}, 0.05)
    assert notes["kalshi logger gap"] == 1
    assert all(e["anchor"] == "polymarket" for e in eps)


def test_the_mapping_check_flags_an_inverted_claim_and_passes_a_true_one():
    k = rows([(t, 0.70, 0.02) for t in range(500, 2500, 10)])
    good = rows([(t, 0.70, 0.02) for t in range(500, 2500, 10)])
    bad = rows([(t, 0.30, 0.02) for t in range(500, 2500, 10)])
    o, q = claim(k, good)
    flagged, checked = X.mapping_check({"o1": o}, q)
    assert flagged == [] and checked == [("polymarket", 0.0)]
    o, q = claim(k, bad)
    flagged, checked = X.mapping_check({"o1": o}, q)
    assert len(flagged) == 1 and flagged[0]["median_gap"] == 0.4


def book_rows(snaps):
    """Odds API rows: one Over per snapshot, raw price."""
    return [(t, None, None, p, "live", None, "Over") for t, p in snaps]


def bracket_claim(k_path, book_snaps):
    o = {"id": "o2", "event_id": "g2", "key": "nfl|2026|wk2|player_prop|00-1|receptions|4.5|over",
         "kick": 100_000.0, "joined": "store",
         "venues": {"kalshi": [("kalshi", "K")], "book": [("oddsapi:dk", "B")]}}
    return o, {("kalshi", "K"): rows(k_path), ("oddsapi:dk", "B"): book_rows(book_snaps)}


def test_bracketed_order_needs_the_other_venue_a_whole_bracket_apart():
    snaps = [(10_000, 0.40), (20_000, 0.50), (30_000, 0.50), (40_000, 0.50)]
    # Kalshi moves in the bracket AFTER the book's: the book was first
    k_late = [(t, 0.40 if t < 25_000 else 0.50, 0.02) for t in range(9_000, 41_000, 300)]
    eps, _ = X.bracket_episodes(*bracket_claim(k_late, snaps), 0.05)
    assert [e["where"] for e in eps] == ["book first (kalshi followed a bracket later)"]
    # Kalshi moves inside the SAME bracket: order unresolvable, never a lead
    k_same = [(t, 0.40 if t < 15_000 else 0.50, 0.02) for t in range(9_000, 41_000, 300)]
    eps, _ = X.bracket_episodes(*bracket_claim(k_same, snaps), 0.05)
    assert [e["where"] for e in eps] == ["same bracket (order unresolvable)"]
    assert eps[0]["first"] is None


def test_the_store_join_misses_a_sign_flipped_spread_and_the_fold_pairs_it():
    con = sqlite3.connect(":memory:")
    con.executescript("""
        CREATE TABLE outcomes (outcome_id TEXT, key TEXT, season INT, week INT, event_id TEXT);
        CREATE TABLE market_outcome (venue TEXT, market_id TEXT, outcome_id TEXT);
        INSERT INTO outcomes VALUES ('k', 'nfl|2026|wk2|spread|buf|na|1.5|over', 2026, 2, 'g'),
                                    ('p', 'nfl|2026|wk2|spread|buf|na|-1.5|over', 2026, 2, 'g'),
                                    ('p2', 'nfl|2026|wk2|spread|buf|na|-2.5|over', 2026, 2, 'g');
        INSERT INTO market_outcome VALUES ('kalshi', 'KXNFLSPREAD-X-BUF2', 'k'),
                                          ('polymarket', 'tok1', 'p'), ('polymarket', 'tok2', 'p2');
    """)
    got = X.cross_pairs(con)
    assert {r[6] for r in got} == {"folded"}                 # the store join paired nothing
    assert sorted((r[4], r[5]) for r in got) == [("kalshi", "KXNFLSPREAD-X-BUF2"), ("polymarket", "tok1")]


def test_after_the_sign_fix_the_store_pairs_spreads_and_the_fold_must_stay_off():
    """a-60 negates the Polymarket line in the mapper. Against a re-mapped store
    a NEGATIVE Polymarket line is an underdog '(+L)' claim, and folding it would
    pair 'Bills +1.5' with 'Bills win by over 1.5' - a wrong pair."""
    con = sqlite3.connect(":memory:")
    con.executescript("""
        CREATE TABLE outcomes (outcome_id TEXT, key TEXT, season INT, week INT, event_id TEXT);
        CREATE TABLE market_outcome (venue TEXT, market_id TEXT, outcome_id TEXT);
        INSERT INTO outcomes VALUES ('k', 'nfl|2026|wk2|spread|buf|na|1.5|over', 2026, 2, 'g'),
                                    ('dog', 'nfl|2026|wk2|spread|buf|na|-1.5|over', 2026, 2, 'g');
        INSERT INTO market_outcome VALUES ('kalshi', 'KXNFLSPREAD-X-BUF2', 'k'),
                                          ('polymarket', 'fav', 'k'), ('polymarket', 'dog', 'dog');
    """)
    got = X.cross_pairs(con, fold=False)
    assert {r[6] for r in got} == {"store"}
    assert sorted((r[4], r[5]) for r in got) == [("kalshi", "KXNFLSPREAD-X-BUF2"), ("polymarket", "fav")]
    assert any(r[5] == "dog" for r in X.cross_pairs(con, fold=True))   # what the fold would have done


def test_release_half_life_times_the_departure_row_not_the_heartbeat():
    # flat at 0.30 with heartbeat rows 300s apart, then a move polled every 15s
    s = [(t, 0.30, 0.31) for t in (0, 300, 600)] + [(900, 0.33, 0.34), (915, 0.37, 0.38), (930, 0.45, 0.46)]
    half, onset = I.half_life(s, 1000, 0.305, 0.455, -1, 2000)
    assert onset == 900 - 1000          # the first departure row, not the 600s heartbeat
    assert half == 930 - 1000
    # a move that never reaches half is not timed
    assert I.half_life([(t, 0.30, 0.31) for t in range(0, 2000, 300)], 1000, 0.305, 0.455, -1, 2000) == (None, None)
