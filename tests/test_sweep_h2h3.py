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


# ---- brief 022 phase 2: CFB replication and week-2 readiness ---------------

import gzip
import io
import json
import os
import subprocess
import sys
from collections import Counter

from research.sweep import h2_imbalance as _H2
from research.sweep import h3_lifecycle as _H3


def _member(obj, fname=None):
    buf = io.BytesIO()
    with gzip.GzipFile(filename=fname or "", mode="wb", fileobj=buf) as g:
        g.write((json.dumps(obj) + "\n").encode())
    return buf.getvalue()


def test_member_recovery_keeps_intact_members_and_drops_a_torn_one():
    a = _member({"n": 1}, "a.jsonl")
    b = _member({"n": 2, "pad": list(range(3000))}, "b.jsonl")
    c = _member({"n": 3}, "c.jsonl")
    data = a + b[: len(b) // 2] + c                  # b torn by an interleaved writer
    stats = Counter()
    got = [json.loads(x) for x in _H2.recover_members(data, stats)]
    assert [g["n"] for g in got] == [1, 3]
    assert stats["failed_starts"] >= 1 and stats["members"] == 2


def test_member_recovery_on_a_clean_stream_recovers_everything():
    data = b"".join(_member({"n": i}) for i in range(20))
    stats = Counter()
    assert len(list(_H2.recover_members(data, stats))) == 20
    assert stats["failed_starts"] == 0 and stats["bytes"] == len(data)


def test_book_touch_takes_the_best_level_on_both_bid_ladders():
    book = {"orderbook_fp": {"yes_dollars": [["0.40", "7"], ["0.45", "30"], ["0.10", "999"]],
                             "no_dollars": [["0.50", "11"], ["0.52", "4"]]}}
    yb, ybs, ya, yas = _H2.book_touch(book)
    assert (yb, ybs) == (0.45, 30.0)
    assert ya == pytest.approx(0.48) and yas == 4.0
    assert _H2.book_touch({"orderbook_fp": {"yes_dollars": [["0.4", "1"]]}}) is None


def test_thinning_keeps_one_instant_per_window():
    ts = np.array([0.0, 30.0, 56.0, 60.0, 200.0])
    I = np.arange(5, dtype=float)
    t2, i2 = _H2.thin(ts[::-1].copy(), I[::-1].copy())
    assert list(t2) == [0.0, 56.0, 200.0] and list(i2) == [0.0, 2.0, 4.0]


def test_cfb_contrast_labels_are_exactly_the_week1_labels():
    path = os.path.join(S.ROOT, "research", "sweep", "results", "h3.jsonl")
    with open(path, encoding="utf-8") as f:
        wk1 = {json.loads(l)["name"].split("|", 1)[1] for l in f
               if json.loads(l)["family"] == "H3 contrast" and json.loads(l)["name"].startswith("KXNFLSPREAD|")}
    acc = _H3.SeriesAcc(3, dated=True)
    assert {label for _n, label, *_ in _H3.contrast_tests("KXNCAAFSPREAD", acc)} == wk1


def test_nfl_analogues():
    assert _H2.nfl_analogue("KXNCAAFSPREAD") == "KXNFLSPREAD"
    assert _H2.nfl_analogue("KXNCAAFTEAMTOTAL") is None


def test_week1_defaults_are_unchanged():
    assert _H2.REG_PATH == os.path.join(S.ROOT, "research", "sweep", "results", "h2.jsonl")
    assert _H3.REG_PATH == os.path.join(S.ROOT, "research", "sweep", "results", "h3.jsonl")
    assert S.POPULATION == "nfl_wk1" and S.ROLE == "search"
    assert (S.SEASON, S.WEEK) == (2026, 1)
    assert S.undated_window()[1] == S.UNDATED_CUTOFF_TS
    import inspect
    d = inspect.signature(_H2.load_games).parameters
    assert (d["season"].default, d["week"].default) == (2026, 1)
    assert _H2.CFB_REG_PATH.endswith(os.path.join("results", "h2_cfb.jsonl"))
    assert _H3.CFB_REG_PATH.endswith(os.path.join("results", "h3_cfb.jsonl"))


def test_week2_population_moves_paths_and_role_without_touching_week1(monkeypatch):
    """A week-2 IMPORT refuses until week 2 settles (common.open_population runs
    at import), so this checks the switch in-process: the registry path and role
    come from common at call time, and both modules delegate to it rather than
    hard-coding week 1."""
    import inspect
    monkeypatch.setattr(S, "POPULATION", "nfl_wk2")
    assert S.registry_path("h2").endswith("h2_wk2.jsonl")
    assert S.registry_path("h3").endswith("h3_wk2.jsonl")
    for mod, name in ((_H2, "h2"), (_H3, "h3")):
        src = inspect.getsource(mod)
        assert f'REG_PATH = S.registry_path("{name}")' in src
        assert "S.open_population()" in src
        assert "\nREG_PATH = os.path.join" not in src      # week-1 path not hard-coded (CFB_REG_PATH is)
    assert "role=S.ROLE, population=S.POPULATION" in inspect.getsource(_H2.run)
    assert "role=S.ROLE" in inspect.getsource(_H3.run)
    assert "S.undated_window()" in inspect.getsource(_H3.collect_futures)
