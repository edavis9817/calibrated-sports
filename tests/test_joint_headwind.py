"""c-23: research/joint_headwind.py - the two-leg arithmetic and its reader."""
import json

import pytest

from research import joint_headwind as h


def test_zero_cost_is_free_at_one_and_two_legs():
    r = h.two_leg(0.8, 0.0)
    assert r["double_breakeven_pp"] == pytest.approx(0.0)
    assert r["double_return"] == pytest.approx(0.0)


def test_two_legs_cost_more_than_one_in_both_units():
    r = h.two_leg(0.5, 0.0338)
    assert r["double_breakeven_pp"] == pytest.approx(100 * (0.5338 ** 2 - 0.25))
    assert r["double_return"] < r["single_return"] < 0


def test_deep_itm_breakeven_exceeds_near_money_breakeven():
    # same per-leg cost: a higher p makes the compounded cross term p*c larger
    assert h.two_leg(0.8, 0.05)["double_breakeven_pp"] > h.two_leg(0.5, 0.05)["double_breakeven_pp"]


def test_out_of_range_refuses():
    with pytest.raises(ValueError):
        h.two_leg(0.98, 0.05)


def test_reader_refuses_a_short_file(tmp_path):
    f = tmp_path / "x.json"
    f.write_text(json.dumps({"results": [{"price": "all", "buckets": []}]}))
    with pytest.raises(ValueError):
        h.rows(json.loads(f.read_text()))


def test_reads_the_committed_aggregates():
    out = h.rows(json.loads(h.SOURCE.read_text()))
    assert len(out) == 10
    deep = [r for r in out if r["p"] >= 0.60]
    assert len(deep) == 3
    # deep in the money, the compounded bar is above the single-leg bar in pp
    assert all(r["double_breakeven_pp"] > r["single_breakeven_pp"] for r in deep)
    assert sum(r["n"] for r in out) == 92_577
