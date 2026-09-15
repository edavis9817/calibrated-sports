"""Brief 022: the fences and statistics every sweep module shares."""
import json

import pytest

from research.sweep import common as S


def test_week1_dated_tickers_are_in_at_any_time():
    assert S.in_search_set("KXNFLREC-26SEP13BUFHOU-BUFDKINCAID86-3", 1_800_000_000)
    assert S.in_search_set("KXNFLSPREAD-26SEP14DENKC-KC4", 0)


def test_week2_tickers_are_never_in_and_raise():
    assert not S.in_search_set("KXNFLSPREAD-26SEP17DETBUF-BUF4", 0)
    with pytest.raises(S.HoldoutViolation):
        S.assert_search_set("KXNFLTOTAL-26SEP20NOBAL-45", 0)


def test_undated_series_are_fenced_by_time():
    assert S.in_search_set("KXNFLWINS-MIA-9", S.UNDATED_CUTOFF_TS - 1)
    assert not S.in_search_set("KXNFLWINS-MIA-9", S.UNDATED_CUTOFF_TS)
    with pytest.raises(S.HoldoutViolation):
        S.assert_search_set("KXNFLAFCCHAMP-26-BUF", S.UNDATED_CUTOFF_TS + 5)


def test_week1_sql_predicate():
    sql, params = S.week1_filter_sql()
    assert sql.count("LIKE") == 4 and params[0] == "%-26SEP09%"


def test_bh_matches_the_textbook_example():
    p = [0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205, 0.212, 0.216]
    assert S.bh(p, q=0.05) == [True, True] + [False] * 8
    assert sum(S.bh(p, q=0.10)) == 6              # p6 = 0.06 <= 6/10 x 0.10


def test_bh_on_pure_noise_rarely_keeps_anything():
    import random
    rng = random.Random(1)
    p = [rng.random() for _ in range(400)]
    assert sum(S.bh(p, 0.10)) <= 2
    assert sum(x < 0.05 for x in p) > 10          # but nominal p finds plenty


def test_boot_p_value_is_not_floored_by_the_draw_count():
    # games genuinely differ (means 0.925..1.075), so the bootstrap SE is > 0
    rows = [{"game": g, "v": 1.0 + 0.05 * ((g % 4) - 1.5) + 0.01 * i}
            for g in range(16) for i in range(5)]
    res = S.boot(rows, S.mean_of("v"))
    assert res["se"] > 0
    assert res["p"] < 1e-6                        # a 2000-draw percentile p could not do this


def test_boot_resamples_games():
    rows = [{"game": g, "v": v} for g, v in (("a", 1.0), ("b", -1.0), ("c", 0.5))]
    dup = S.boot([dict(r) for r in rows for _ in range(30)], S.mean_of("v"))
    assert dup["games"] == 3 and dup["n"] == 90


def test_wilson_is_not_the_normal_approximation_at_small_counts():
    lo, hi = S.wilson(0, 20)
    assert lo == pytest.approx(0.0, abs=1e-12) and hi > 0.1


def test_registry_and_summary(tmp_path):
    reg = S.Registry(str(tmp_path / "r.jsonl"))
    reg.add("fam", "strong", {"est": 0.05, "lo": 0.03, "hi": 0.07, "se": 0.01, "p": 1e-6, "n": 100, "games": 16})
    reg.add("fam", "noise", {"est": 0.01, "lo": -0.02, "hi": 0.04, "se": 0.015, "p": 0.5, "n": 100, "games": 16})
    reg.add("fam", "n/a", None)
    reg.add("desc", "median spread", {"est": 3.0, "lo": 2.0, "hi": 4.0, "se": 0.5, "p": 0.0, "n": 9, "games": 9},
            role="descriptive")
    recs = S.load_registries([str(tmp_path / "r.jsonl")])
    s = S.summarize(recs)
    assert s["search_tests"] == 2 and s["search_not_estimable"] == 1
    assert s["bh_survivors"] == 1 and s["descriptive"] == 1
    assert s["expected_false_positives"] == pytest.approx(0.1)


def test_holdout_is_closed_until_the_candidates_doc_is_committed(tmp_path, monkeypatch):
    with pytest.raises(S.HoldoutViolation):
        S.require_committed("docs/briefs/does-not-exist-022.md")


def test_every_cfb_accessor_goes_through_the_guard(monkeypatch):
    monkeypatch.setattr(S, "CANDIDATES_DOC", "docs/briefs/does-not-exist-022.md")
    with pytest.raises(S.HoldoutViolation):
        S.cfb_ro()
    with pytest.raises(S.HoldoutViolation):
        S.cfb_raw_dir()
