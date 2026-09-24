"""F12's estimator must be able to return the answer it did not return.

The registered test came back null. A null is only a result if the same code, fed
a market that DOES overreact, returns a negative coefficient - and fed one that
under-reacts, a positive one. Synthetic data only; no store is opened.
"""
import collections

import numpy as np

from research import f12_movement_test as t


def _rows(k, n=4000, seed=1):
    """Moves D; the true expectation moves by (1 + k) * D, so e = y - close has slope k.

    k < 0: the line moved further than the truth (overreaction).
    k > 0: the line moved less than the truth (under-reaction).
    """
    rng = np.random.default_rng(seed)
    d = rng.choice([-3, -2, -1.5, -1, -0.5, 0, 0.5, 1, 1.5, 2, 3], n)
    e = k * d + rng.normal(0, 3, n)
    return [dict(d=float(a), e=float(b), cluster=(2024, i % 40)) for i, (a, b) in enumerate(zip(d, e))]


def test_overreaction_reads_negative():
    est = t.estimate(_rows(-0.5), np.random.default_rng(0))
    assert est["slope"]["hi"] < 0
    assert est["bands"]["(2.5, inf)"]["mean_signed_e"]["est"] < 0


def test_underreaction_reads_positive():
    est = t.estimate(_rows(+0.5), np.random.default_rng(0))
    assert est["slope"]["lo"] > 0


def test_efficient_interval_contains_zero():
    est = t.estimate(_rows(0.0), np.random.default_rng(0))
    assert est["slope"]["lo"] < 0 < est["slope"]["hi"]


def test_cover_z_is_against_one_half():
    est = t.estimate(_rows(0.0), np.random.default_rng(0))
    c = est["bands"]["(1, 2.5]"]["toward_side_covers_close"]
    assert c["null"] == 0.5 and abs(c["z"]) < 4


def test_pick_takes_nearest_to_target_inside_window():
    ko = 1000 * 3600.0
    ts = [ko - h * 3600 for h in (600, 200, 170, 130, 30, 0.2)]
    assert t.pick(ts, ko, t.OPEN_WIN, t.OPEN_TARGET) == ko - 170 * 3600
    assert t.pick(ts, ko, t.LATE_WIN, t.LATE_TARGET) == ko - 30 * 3600
    assert t.pick([ko - 600 * 3600], ko, t.OPEN_WIN, t.OPEN_TARGET) is None


def test_move_uses_common_books_only():
    """A book present only at the close must not create a move."""
    h = 3600.0
    ko = 10_000 * h
    games = {"g": dict(season=2024, week=1, ko=ko, home="A", away="B", margin=3, total=40)}
    snap = {"spread": {"g": {ko - 168 * h: {"b1": 3.0, "b2": 3.0},
                             ko - 0.2 * h: {"b1": 3.0, "b2": 3.0, "b3": 10.0}}},
            "total": {"g": {}}}
    snap = {k: collections.defaultdict(dict, v) for k, v in snap.items()}
    rows, _ = t.build_p(games, snap)[("spread", "open")]
    assert len(rows) == 1 and rows[0]["d"] == 0.0
