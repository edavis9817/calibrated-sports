"""Brief 021 Part A: scoring pieces, on synthetic numbers only."""
import math
import statistics

import pytest

from research import score as S


def test_brier_and_logloss():
    assert S.brier(0.7, 1) == pytest.approx(0.09)
    assert S.logloss(0.5, 0) == pytest.approx(math.log(2))
    assert S.logloss(0.0, 1) == pytest.approx(-math.log(S.EPS))     # clipped, finite


def test_naive_player_rate_is_laplace_over_games_with_an_opportunity():
    games = [(5, 7), (2, 4), (0, 0), (6, 8), (4, 5)]          # one game with no target
    p, src = S.naive_prob(games, pooled=(10, 100), line=4.5)
    assert src == "player" and p == pytest.approx((2 + 1) / (4 + 2))


def test_naive_falls_back_to_pooled_under_four_games():
    p, src = S.naive_prob([(5, 7), (1, 2)], pooled=(30, 98), line=4.5)
    assert src == "pooled" and p == pytest.approx(31 / 100)


def test_buckets_are_the_preregistered_edges():
    assert S.bucket(8, S.HISTORY) == "thin 0-8"
    assert S.bucket(9, S.HISTORY) == "medium 9-24"
    assert S.bucket(25, S.HISTORY) == "thick 25+"
    assert S.bucket(0.149, S.PRICE) == "<0.15"
    assert S.bucket(0.15, S.PRICE) == "0.15-0.35"
    assert S.bucket(0.65, S.PRICE) == "0.65-0.85"
    assert S.bucket(0.86, S.PRICE) == ">0.85"


def test_kalshi_rung_is_over_line_minus_half():
    mid = "KXNFLREC-26SEP13DALNYG-NYGOBECKHAM13-6"
    assert S.rung(mid) == 6 and S.rung(mid) - 0.5 == 5.5
    assert S.ladder_key(mid) == "KXNFLREC-26SEP13DALNYG-NYGOBECKHAM13"


def test_ladder_position_uses_rungs_listed_at_entry():
    base = "KXNFLREC-26SEP13DALNYG-NYGOBECKHAM13"
    listed = [f"{base}-{k}" for k in (3, 4, 5, 6)] + ["KXNFLREC-26SEP13DALNYG-OTHER1-9"]
    assert S.ladder_position(f"{base}-3", listed) == "edge"
    assert S.ladder_position(f"{base}-6", listed) == "edge"
    assert S.ladder_position(f"{base}-4", listed) == "interior"
    assert S.ladder_position(f"{base}-7", [f"{base}-3"]) == "edge"   # unlisted self counts


def test_bootstrap_blocks_on_games_and_duplication_does_not_narrow():
    rows = [{"game": g, "d": v} for g, v in (("a", .01), ("b", -.02), ("c", .03), ("d", 0))]
    one = S.boot_mean(rows, lambda r: r["d"])
    dup = S.boot_mean([dict(r) for r in rows for _ in range(25)], lambda r: r["d"])
    assert dup["games"] == 4 and dup["n"] == 100
    assert dup["sd"] == pytest.approx(one["sd"], rel=1e-9)


def test_cluster_se_matches_the_hand_formula():
    rows = [{"game": "a", "d": 1.0}, {"game": "a", "d": 1.0},
            {"game": "b", "d": -1.0}, {"game": "b", "d": -1.0}]
    # mean 0; cluster sums +2, -2; CR1 = sqrt(2/1 * 8)/4 = 1.0
    assert S.cluster_se(rows, lambda r: r["d"]) == pytest.approx(1.0)


def test_mde_and_games_needed_scale_with_root_games():
    assert S.mde(0.01) == pytest.approx(0.028, abs=1e-3)
    g = S.games_for_mde(0.01, 16, S.mde(0.01) / 2)          # halve the MDE
    assert g == pytest.approx(64)


def test_reliability_uses_wilson_and_flags_small_bins():
    ps = [0.05] * 40 + [0.95] * 10
    ys = [0] * 38 + [1] * 2 + [1] * 10
    table, ece = S.reliability(ps, ys)
    lo_bin = table[0]
    assert lo_bin["n"] == 40 and lo_bin["rate"] == pytest.approx(0.05)
    assert lo_bin["wilson"][0] > 0 and not lo_bin["excludes"]
    assert table[9]["n"] == 10 and table[9]["excludes"] is False   # n<30 never flagged
    assert ece == pytest.approx((40 * 0.0 + 10 * 0.05) / 50)


def test_reliability_flags_a_miscalibrated_bin():
    table, _ = S.reliability([0.45] * 100, [1] * 90 + [0] * 10)
    assert table[4]["excludes"] is True


# ------------------------------------------------------------------ a-58: the entry quote

ENTRY = 1_000_000.0
MKT = "KXNFLREC-26SEP13DALNYG-NYGOBECKHAM13-6"


def _quotes(rows):
    """An in-memory quotes table with only the columns the entry lookup reads."""
    import sqlite3
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE quotes (ts REAL, venue TEXT, market_id TEXT, best_bid REAL, "
              "best_ask REAL, source TEXT)")
    c.executemany("INSERT INTO quotes VALUES (?, 'kalshi', ?, ?, ?, ?)", rows)
    return c


def test_a_fresh_live_two_sided_quote_is_the_mid():
    assert S.check_entry((ENTRY - 300, 0.40, 0.44, "live"), ENTRY) == pytest.approx(0.42)


@pytest.mark.parametrize("quote, reason", [
    ((ENTRY - 61_424, 0.40, 0.44, "backfill:kalshi_candles"), S.NON_LIVE),   # the week-1 case
    ((ENTRY - 60, 0.40, 0.44, "backfill:kalshi_candles"), S.NON_LIVE),       # fresh, still not live
    ((ENTRY - S.ENTRY_MAX_AGE - 1, 0.40, 0.44, "live"), S.STALE),
    ((ENTRY - 300, None, 0.44, "live"), S.ONE_SIDED),
    (None, S.NO_QUOTE),
])
def test_a_non_live_or_stale_entry_quote_raises(quote, reason):
    with pytest.raises(S.EntryQuoteRefused) as e:
        S.check_entry(quote, ENTRY)
    assert e.value.reason == reason


def test_the_age_limit_admits_every_measured_live_gap():
    """a-58 measured live gaps up to 712s; the limit must admit them."""
    assert S.ENTRY_MAX_AGE >= 712
    assert S.check_entry((ENTRY - 712, 0.40, 0.44, "live"), ENTRY) == pytest.approx(0.42)


def test_the_lookup_prefers_live_and_never_falls_through_to_a_scored_candle():
    """Pruned live rows leave a candle behind. The lookup may RETURN it - so the
    refusal can name it - but check_entry must refuse it, so nothing is scored."""
    c = _quotes([(ENTRY - 61_424, MKT, 0.40, 0.44, "backfill:kalshi_candles")])
    q = S.entry_quote(c, MKT, ENTRY)
    assert q[3] == "backfill:kalshi_candles"
    with pytest.raises(S.EntryQuoteRefused, match="not live"):
        S.check_entry(q, ENTRY)
    # A NEWER candle does not displace a fresh live quote.
    c = _quotes([(ENTRY - 300, MKT, 0.40, 0.44, "live"),
                 (ENTRY - 10, MKT, 0.10, 0.20, "backfill:kalshi_candles")])
    assert S.check_entry(S.entry_quote(c, MKT, ENTRY), ENTRY) == pytest.approx(0.42)
    # A stale live quote is refused as stale even with a fresher candle on file.
    c = _quotes([(ENTRY - 5_000, MKT, 0.40, 0.44, "live"),
                 (ENTRY - 10, MKT, 0.10, 0.20, "backfill:kalshi_candles")])
    with pytest.raises(S.EntryQuoteRefused) as e:
        S.check_entry(S.entry_quote(c, MKT, ENTRY), ENTRY)
    assert e.value.reason == S.STALE


def scored_row(game, y, model, naive, quote, entry=ENTRY):
    """A row as research.score.load builds it, through the same check."""
    try:
        mid, refused = S.check_entry(quote, entry), None
    except S.EntryQuoteRefused as e:
        mid, refused = None, e.reason
    return {"game": game, "market": MKT, "y": y, "model": model, "naive": naive,
            "market_p": mid, "market_excluded": refused,
            "entry_quote_source": quote[3] if quote else None,
            "entry_quote_age": entry - quote[0] if quote else None}


REGISTERED = {"estimate": 0.024, "interval": [0.0089, 0.0373], "n": 706, "games": 14,
              "register_id": "R10"}


def week1_rows(n_games=6, per_game=5):
    """Every row priced only by a day-old candle - the week-1 state c-24 found."""
    return [scored_row(f"g{g}", float((g + i) % 2), 0.3 + 0.1 * (i % 4), 0.45,
                       (ENTRY - 61_424, 0.40, 0.44, "backfill:kalshi_candles"))
            for g in range(n_games) for i in range(per_game)]


def test_the_fallback_path_cannot_produce_a_scored_row():
    rows = week1_rows()
    assert all(r["market_p"] is None for r in rows)
    pub = S.published(rows, REGISTERED)
    mc = pub["market_comparison"]
    assert mc["status"] == "withdrawn" and mc["scorable"] == 0 and mc["settled"] == len(rows)
    assert mc["excluded"] == [{"reason": S.NON_LIVE, "n": len(rows)}]
    assert mc["refused_quote_sources"] == [{"source": "backfill:kalshi_candles", "n": len(rows)}]
    assert mc["refused_quote_median_age_s"] == pytest.approx(61_424)
    # every market figure is withdrawn; the model-only figures stand on every settled row
    assert pub["brier"]["market"] is None and pub["model_minus_market"] is None
    assert pub["ece"]["market"] is None
    assert [s["name"] for s in pub["series"]] == ["model"]
    assert pub["n"] == len(rows) and pub["brier"]["model"] is not None
    assert pub["model_minus_naive"] is not None
    reg = mc["registered"]
    assert reg["re_derivable"] is False and reg["n"] == 706 and reg["estimate"] == 0.024
    assert f"0 are scorable against the market, against 706" in reg["why"]


def test_a_live_scorable_set_publishes_on_that_set_only():
    """The other answer: the same pipeline publishes when live quotes exist, and
    every figure is on the scorable rows - never on the refused ones."""
    rows = week1_rows()
    live = [scored_row(f"g{g}", float(g % 2), 0.6, 0.45, (ENTRY - 300, 0.40, 0.44, "live"))
            for g in range(6)]
    pub = S.published(rows + live, REGISTERED)
    mc = pub["market_comparison"]
    assert mc["status"] == "published" and mc["scorable"] == 6 and pub["n"] == 6
    assert [s["name"] for s in pub["series"]] == ["model", "market"]
    assert pub["brier"]["market"] == pytest.approx(statistics.fmean(
        (0.42 - r["y"]) ** 2 for r in live))
    assert pub["model_minus_market"] is not None
    assert mc["registered"]["re_derivable"] is False          # 6 is not the registered 706
