"""Brief 022 H2/H3: the pure pieces of the imbalance and lifecycle sweeps."""
from datetime import datetime

import numpy as np
import pytest

from research.sweep import common as S
from research.sweep import h2_imbalance as H2
from research.sweep import h3_lifecycle as H3


def test_imbalance_needs_both_sides():
    assert H2.imbalance(300, 100) == pytest.approx(0.5)
    assert H2.imbalance(100, 300) == pytest.approx(-0.5)
    assert H2.imbalance(0, 100) is None and H2.imbalance(None, 5) is None


def test_tier_boundaries():
    assert H2.tier(99, 100) == "pre"
    assert H2.tier(100, 100) == "in"
    assert H2.tier(100 + 4 * 3600, 100) == "in"
    assert H2.tier(101 + 4 * 3600, 100) is None


def test_asof_respects_staleness():
    qts = [0.0, 100.0, 900.0]
    assert H2.asof_index(qts, 150) == 1
    assert H2.asof_index(qts, 100 + 661) is None
    assert H2.asof_index(qts, -1) is None


def test_event_to_game_handles_kalshi_codes():
    k = datetime(2026, 9, 10, 20, 35, tzinfo=S.ET).timestamp()
    games = {"2026_01_SF_LA": (k, "LA", "SF"),
             "2026_01_CLE_JAX": (k + 3 * 86400, "JAX", "CLE")}
    assert H2.event_game("KXNFLSPREAD-26SEP10SFLAR", games) == "2026_01_SF_LA"
    assert H2.event_game("KXNFLTOTAL-26SEP13CLEJAC", games) == "2026_01_CLE_JAX"
    assert H2.event_game("KXNFLTOTAL-26SEP20CLEJAC", games) is None     # wrong week


def test_week1_markets_refuses_week2_tickers():
    class C:
        def execute(self, sql, params):
            return iter([("KXNFLSPREAD-26SEP17DETBUF-BUF4", "KXNFLSPREAD-26SEP17DETBUF")])
    with pytest.raises(S.HoldoutViolation):
        H2.week1_markets(C(), "KXNFLSPREAD")


def test_sufficient_statistic_slope_equals_ols():
    rng = np.random.default_rng(0)
    g = rng.integers(0, 6, 500)
    x = rng.normal(size=500)
    y = 0.3 * x + rng.normal(size=500)
    rows = H2.slope_rows(H2.game_suffstats(g, x, y, 6))
    assert H2.slope_stat(rows) == pytest.approx(np.polyfit(x, y, 1)[0])


def test_mean_rows_pool_correctly():
    g = np.array([0, 0, 1])
    v = np.array([1.0, 3.0, 5.0])
    assert H2.mean_stat(H2.mean_rows(g, v, 2)) == pytest.approx(3.0)


def test_grid_is_time_weighted_and_drops_stale_samples():
    qts = [0, 30, 2000]              # a gap longer than MAX_AGE between 30 and 2000
    bids, asks = [0.40, 0.41, 0.45], [0.42, 0.43, 0.47]
    kick = 1000
    g, w, spread, mid = H3.sample_grid(qts, bids, asks, kick=kick)
    assert set(w[g < kick]) == {60.0}
    assert not np.any((g > 30 + 660) & (g < 2000))          # stale stretch dropped
    assert np.all(w[g >= kick] == 10.0)
    assert np.allclose(spread, 0.02)


def test_one_sided_book_is_not_sampled():
    g, w, spread, mid = H3.sample_grid([0], [np.nan], [0.5], kick=None, end_cap=600)
    assert len(g) == 0


def test_ttk_buckets():
    K = 1_789_000_000
    grid = [K - 80 * 3600, K - 30 * 3600, K - 10 * 3600, K - 2 * 3600, K - 1800, K, K + 10]
    assert list(H3.ttk_bucket(grid, kick=K)) == [0, 1, 2, 3, 4, 5, 5]


def test_hour_and_dow_match_zoneinfo_for_september():
    ts = datetime(2026, 9, 13, 13, 30, tzinfo=S.ET).timestamp()      # Sunday 1:30pm EDT
    hour, dow, _day = H3.hour_dow_et([ts])
    assert hour[0] == 13 and H3.DOW[dow[0]] == "Sun"


def test_hist_median_and_contrast():
    h = np.zeros((2, 3, 5))                      # 2 groups, 3 blocks, cents 0..4
    h[0, :, 1] = 10                              # group A: spread 1c everywhere
    h[1, :, 3] = 10                              # group B: spread 3c
    assert H3.weighted_median_from_hist(h[1].sum(axis=0)) == 3
    rows = H3.contrast_rows(h, (0,), (1,))
    assert H3.contrast_stat(rows) == pytest.approx(-2.0)
    res = H3.hist_boot_median(h[0])
    assert res["est"] == 1 and res["games"] == 3
