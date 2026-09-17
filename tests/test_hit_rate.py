"""A hit rate counts each market ONCE. Run: pytest -q tests/test_hit_rate.py

THE CLASS OF WRONG NUMBER THIS PREVENTS. A settled prop exists twice in
`outcomes` - once as the over, once as the under - and `outcome_settlement`
records the MARKET's result, identical on both rows (measured 2026-09-17:
97,948 of 97,948 two-sided markets agree). So a hit rate computed over both rows
counts every game twice:

    receptions, over rows only    n=21,914   rate 0.4275
    receptions, under rows only   n=20,031   rate 0.4383
    receptions, pooled            n=41,945   rate 0.4327

The RATE barely moves. What doubles is `n`, so every interval built on it is
about sqrt(2) too narrow and the sample looks twice the size it is - the same
class as brief 021's "the effective sample is 136 fits, not 935".

A SECOND, DIFFERENT FAILURE shares the shape: asking "did THIS ROW's side win"
over both sides returns exactly 0.5000 in every slice, because over and under
are exact complements. That is the one CLAUDE.md warns about. One rule stops
both - count each market once - which is why the guard is keyed on the market
and not on the side.

WHY NOT JUST FILTER `side == 'over'`. 4,211 markets are one-sided, including
1,800 receptions markets that carry only an under row; filtering would drop them
silently. `anytime_td`, `passing_yards` and `rush_yards` have no under rows at
all - one-sided by construction. Since `result` is market-level, the over-view
is recoverable from either row, so the guard normalises rather than filters.

Same shape as `cfb.guards` and `analytics.gate`: a pure function returning
violations, and a test that drives it into every banned shape. A guard that has
never been seen to fail is not a guard.
"""
import pytest

from core import settlement as S
from core import stats


def row(season=2025, week=1, entity_id="00-A", stat="receptions", line=4.5,
        side="over", result="over"):
    return {"season": season, "week": week, "entity_id": entity_id,
            "stat": stat, "line": line, "side": side, "result": result}


# ------------------------------------------------------- the violation finder

def test_a_clean_population_has_no_violations():
    """Discriminating: the finder must be capable of returning nothing, or
    every other assertion here is satisfied by a function that always fires."""
    assert stats.pooled_side_violations([row(week=1), row(week=2), row(week=3)]) == []


def test_both_sides_of_one_market_is_a_violation():
    v = stats.pooled_side_violations([row(side="over"), row(side="under")])
    assert len(v) == 1
    key, sides = v[0]
    assert sides == ["over", "under"]
    assert key == (2025, 1, "00-A", "receptions", 4.5), key


def test_the_same_market_twice_on_ONE_side_is_also_a_violation():
    """Duplication does not need two sides to inflate n. A re-parse or a bad
    join can emit the same row twice, and the count is wrong either way."""
    assert stats.pooled_side_violations([row(side="over"), row(side="over")])


def test_markets_differing_in_ANY_key_field_are_distinct():
    """The key is (season, week, entity_id, stat, line). Two lines on the same
    player in the same game are two claims, not a duplicate - a ladder prices
    several, and collapsing them would undercount."""
    for field, other in (("season", 2024), ("week", 2), ("entity_id", "00-B"),
                         ("stat", "rush_attempts"), ("line", 5.5)):
        assert stats.pooled_side_violations([row(), row(**{field: other})]) == [], field


def test_the_violation_NAMES_the_market():
    """A guard that says only 'something is duplicated' sends someone hunting.
    The 09-11 duplicate survived a day for want of a name."""
    v = stats.pooled_side_violations([row(entity_id="00-Z", line=7.5),
                                      row(entity_id="00-Z", line=7.5, side="under")])
    assert "00-Z" in str(v[0][0])


# ------------------------------------------------------------- the hit rate

def test_hit_rate_refuses_a_pooled_population():
    with pytest.raises(stats.PooledSides) as e:
        stats.hit_rate([row(side="over"), row(side="under")])
    assert "00-A" in str(e.value)


def test_hit_rate_counts_each_market_once():
    rows = [row(week=1, result="over"), row(week=2, result="under"),
            row(week=3, result="over")]
    r = stats.hit_rate(rows)
    assert r["n"] == 3 and r["cleared"] == 2
    assert r["rate"] == pytest.approx(2 / 3)


def test_the_over_view_is_recoverable_from_an_UNDER_row():
    """`result` is the MARKET's outcome, identical on both rows, so a market
    that logged only its under side still answers 'did the over clear'. This is
    why the guard normalises instead of filtering on side."""
    only_under = [row(side="under", result="over")]
    assert stats.hit_rate(only_under)["cleared"] == 1


def test_a_one_sided_market_is_KEPT_not_dropped():
    """1,800 receptions markets carry only an under row; anytime_td has no
    under rows at all. A `side == 'over'` filter would silently lose them."""
    rows = [row(stat="anytime_td", line=0.5, side="over", result="over"),
            row(stat="receptions", side="under", result="under")]
    assert stats.hit_rate(rows)["n"] == 2


def test_cleared_means_the_OVER_cleared_per_the_settlement_rule():
    """One definition of OVER in this repo, not two."""
    assert stats.hit_rate([row(result=S.OVER)])["cleared"] == 1
    assert stats.hit_rate([row(result=S.UNDER)])["cleared"] == 0


def test_an_empty_population_states_no_rate():
    r = stats.hit_rate([])
    assert r["n"] == 0 and r["cleared"] == 0
    assert r["rate"] is None, "0/0 must not render as 0.0"


def test_the_interval_comes_with_the_rate():
    """No estimate without its interval and its n - the same rule track F
    enforces in `analytics.gate`."""
    r = stats.hit_rate([row(week=w, result="over" if w < 8 else "under")
                        for w in range(1, 18)])
    assert r["n"] == 17 and r["cleared"] == 7
    assert r["lo"] < r["rate"] < r["hi"]
    assert 0.0 <= r["lo"] and r["hi"] <= 1.0


def test_pooling_would_have_narrowed_the_interval_and_the_guard_stops_it():
    """The harm, demonstrated rather than asserted: doubling every row leaves
    the rate alone and shrinks the interval. The guard raises instead."""
    clean = [row(week=w, result="over" if w < 8 else "under") for w in range(1, 18)]
    honest = stats.hit_rate(clean)
    doubled = [dict(r) for r in clean] + [dict(r, side="under") for r in clean]
    with pytest.raises(stats.PooledSides):
        stats.hit_rate(doubled)
    # and if it had NOT raised, this is what it would have published:
    naive_n = len(doubled)
    assert naive_n == 2 * honest["n"], "the pooled population is exactly twice the size"
