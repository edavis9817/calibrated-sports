"""Brief 022 phase 2: the pure pieces of the CFB scan."""
from research import cfb_coherence as coh
from research.sweep import scan_cfb as C


def test_lowest_rung_per_team():
    rows = [("A-2", "MIA", 2.5), ("A-1", "MIA", 1.5), ("B-3", "FAMU", 3.5), ("X", "FAMU", None)]
    assert C.lowest_rungs(rows) == {"MIA": ("A-1", 1.5), "FAMU": ("B-3", 3.5)}


def test_touch_ok_sides_window_and_size():
    raw = [(100.0, 0.40, 50.0, 0.43, 5.0), (150.0, 0.41, 8.0, 0.44, 30.0)]
    assert C.touch_ok(raw, 160, "buy_yes", 0.44)                 # ask 0.44, 30 contracts
    assert not C.touch_ok(raw, 160, "buy_yes", 0.43)             # different book
    assert not C.touch_ok(raw, 160, "buy_no", 0.59)              # bid 0.41 but only 8
    assert C.touch_ok(raw, 120, "buy_no", 0.60)                  # bid 0.40, 50 contracts
    assert not C.touch_ok(raw, 300, "buy_yes", 0.44)             # older than 60s
    assert not C.touch_ok(raw, 50, "buy_yes", 0.43)              # nothing before t


def test_relation_rows_keep_only_the_four_and_flag_band_violations(monkeypatch):
    fake = {"g1": "x"}
    monkeypatch.setattr(coh, "relations", lambda g: [
        {"name": "Q1+Q2 = 1H", "dev": 1.0, "band": 0.5},
        {"name": "teamA+teamB = game total", "dev": 0.2, "band": 0.5},
        {"name": "Q3+Q4 = game-1H", "dev": 9.0, "band": 0.1}])
    rows, other = C.relation_rows(fake)
    assert [(r["relation"], r["viol"]) for r in rows] == [("Q1+Q2 = 1H", 1.0), ("teamA+teamB = game total", 0.0)]
    assert other == {"Q3+Q4 = game-1H": 1}


def test_snapshot_patch_passes_the_override_and_restores():
    orig = coh.load_snapshot
    seen = {}

    def spy(conn, series, lo, hi, ts_by_game=None):
        seen["ts"] = ts_by_game
        return {}
    coh.load_snapshot = spy
    try:
        with C.snapshot_before({"G": 5.0}):
            coh.load_snapshot(None, "S", 0, 1)
        assert seen["ts"] == {"G": 5.0}
        assert coh.load_snapshot is spy
    finally:
        coh.load_snapshot = orig


def test_wilson_record_is_descriptive_shape():
    r = C.wilson_res(3, 59, 59)
    assert 0 < r["lo"] < 3 / 59 < r["hi"] and r["n"] == 59
