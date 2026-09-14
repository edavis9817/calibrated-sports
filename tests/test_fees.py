"""Kalshi fees, against the schedule's own published table.

Run: pytest -q tests/test_fees.py

The 42 vectors below are transcribed from the General Trading Fees Table in the
fee schedule PDF (effective 2026-07-07), not computed from the formula - so
they test the implementation against Kalshi rather than against itself.

Three of them discriminate the two rounding rules the schedule describes
inconsistently. At 100 contracts P = 0.25 / 0.35 / 0.45 the raw fees are
1.3125 / 1.5925 / 1.7325; the published table shows 1.32 / 1.60 / 1.74, which
is ceil-to-CENT. The prose says centicent. See `core/fees.py`.
"""
from decimal import Decimal

import pytest

from core.fees import (edge_after_fees, fee_per_contract, kalshi_fee, listed,
                       series_multiplier, series_of)

# (price, fee) from the schedule, 1 contract, taker, M = 1
ONE = [
    (0.01, "0.01"), (0.05, "0.01"), (0.10, "0.01"), (0.15, "0.01"),
    (0.20, "0.02"), (0.25, "0.02"), (0.30, "0.02"), (0.35, "0.02"),
    (0.40, "0.02"), (0.45, "0.02"), (0.50, "0.02"), (0.55, "0.02"),
    (0.60, "0.02"), (0.65, "0.02"), (0.70, "0.02"), (0.75, "0.02"),
    (0.80, "0.02"), (0.85, "0.01"), (0.90, "0.01"), (0.95, "0.01"),
    (0.99, "0.01"),
]

# ... and 100 contracts
HUNDRED = [
    (0.01, "0.07"), (0.05, "0.34"), (0.10, "0.63"), (0.15, "0.90"),
    (0.20, "1.12"), (0.25, "1.32"), (0.30, "1.47"), (0.35, "1.60"),
    (0.40, "1.68"), (0.45, "1.74"), (0.50, "1.75"), (0.55, "1.74"),
    (0.60, "1.68"), (0.65, "1.60"), (0.70, "1.47"), (0.75, "1.32"),
    (0.80, "1.12"), (0.85, "0.90"), (0.90, "0.63"), (0.95, "0.34"),
    (0.99, "0.07"),
]


@pytest.mark.parametrize("price,fee", ONE)
def test_published_table_one_contract(price, fee):
    assert kalshi_fee(price, 1) == Decimal(fee)


@pytest.mark.parametrize("price,fee", HUNDRED)
def test_published_table_one_hundred_contracts(price, fee):
    assert kalshi_fee(price, 100) == Decimal(fee)


def test_all_42_vectors_are_actually_covered():
    """A guard on the guard: 21 + 21, and no duplicated price silently
    shrinking the table."""
    assert len(ONE) == 21 and len(HUNDRED) == 21
    assert len({p for p, _ in ONE}) == 21
    assert len({p for p, _ in HUNDRED}) == 21


# =============================================================================
# the three vectors that decide the rounding rule
# =============================================================================

@pytest.mark.parametrize("price,raw,table", [
    (0.25, "1.3125", "1.32"),
    (0.35, "1.5925", "1.60"),
    (0.45, "1.7325", "1.74"),
])
def test_rounding_is_to_the_cent_not_the_centicent(price, raw, table):
    """The schedule's prose and its table disagree. These three are where the
    disagreement is visible, and the table wins. Centicent rounding would give
    1.3125 / 1.5925 / 1.7325 unchanged."""
    p = Decimal(str(price))
    exact = Decimal("0.07") * 100 * p * (1 - p)
    assert exact == Decimal(raw)
    assert kalshi_fee(price, 100) == Decimal(table)


def test_exact_cent_values_are_not_rounded_up():
    """THE FLOAT BUG THIS MODULE EXISTS TO AVOID. In binary,
    0.07*100*0.1*0.9 is 0.6300000000000001 and a ceil turns $0.63 into $0.64.
    Every one of these is exact in decimal and must come back unchanged."""
    for price, fee in [(0.10, "0.63"), (0.20, "1.12"), (0.30, "1.47"),
                       (0.40, "1.68"), (0.50, "1.75")]:
        assert kalshi_fee(price, 100) == Decimal(fee), price


# =============================================================================
# maker, and the multiplier
# =============================================================================

@pytest.mark.parametrize("price", [p for p, _ in HUNDRED])
def test_maker_with_no_multiplier_is_exactly_zero(price):
    """Maker default M = 0, and M = 0 means free - not one cent."""
    for c in (1, 10, 100, 5000):
        assert kalshi_fee(price, c, side="maker") == Decimal("0")


def test_maker_with_a_multiplier_is_a_quarter_of_taker_before_rounding():
    """0.0175 / 0.07 = 1/4. At 10,000 contracts the rounding is negligible so
    the ratio shows through."""
    t = kalshi_fee(0.50, 10000, side="taker")
    m = kalshi_fee(0.50, 10000, side="maker", multiplier=1)
    assert m == t / 4


def test_an_explicit_zero_multiplier_beats_the_taker_default():
    assert kalshi_fee(0.50, 100, side="taker", multiplier=0) == Decimal("0")


def test_the_combo_series_charges_makers_double():
    maker_m, taker_m = series_multiplier("KXMVE")
    assert (maker_m, taker_m) == (Decimal(2), Decimal(1))
    assert kalshi_fee(0.50, 100, side="maker", multiplier=maker_m) == \
        kalshi_fee(0.50, 100, side="maker", multiplier=1) * 2


# =============================================================================
# the series table
# =============================================================================

def test_series_is_taken_from_the_ticker_prefix():
    assert series_of("KXNFLREC-26SEP13DALNYG-NYGOBECKHAM13-6") == "KXNFLREC"
    assert series_of("KXNFLGAME-26SEP13DALNYG-DAL") == "KXNFLGAME"
    assert series_of("") == ""


@pytest.mark.parametrize("ticker", [
    "KXNFLGAME", "KXNCAAFGAME", "KXSB", "KXNFLMVP", "KXNFLCOTY",
    "KXNFLOPOTY", "KXNFLDPOTY", "KXNFLOROTY", "KXNFLDROTY", "KXNFLCPOTY",
])
def test_listed_football_series_are_one_and_one(ticker):
    assert series_multiplier(ticker) == (Decimal(1), Decimal(1))
    assert listed(ticker) is True


@pytest.mark.parametrize("ticker", [
    "KXNFLAFCEAST", "KXNFLAFCWEST", "KXNFLNFCNORTH", "KXNFLAFCCHAMP",
])
def test_division_and_conference_series_match_by_prefix(ticker):
    """These are a family, not an enumerable list."""
    assert series_multiplier(ticker) == (Decimal(1), Decimal(1))
    assert listed(ticker) is True


@pytest.mark.parametrize("ticker", [
    "KXNFLREC", "KXNFLRSHATT", "KXNFLTOTAL", "KXNFLSPREAD", "KXNFLFFPTS",
])
def test_player_prop_series_are_UNLISTED_so_makers_are_free(ticker):
    """The load-bearing fact from the schedule: no NFL player-prop series
    appears in the Non-Standard Fees table, so the published default applies
    and a resting order on one costs nothing."""
    maker_m, taker_m = series_multiplier(ticker)
    assert listed(ticker) is False
    assert (maker_m, taker_m) == (Decimal(0), Decimal(1))
    assert kalshi_fee(0.50, 100, side="maker", multiplier=maker_m) == Decimal("0")
    assert kalshi_fee(0.50, 100, side="taker", multiplier=taker_m) == Decimal("1.75")


def test_a_full_market_id_resolves_like_its_series():
    assert series_multiplier("KXNFLGAME-26SEP13DALNYG-DAL") == \
        series_multiplier("KXNFLGAME")


# =============================================================================
# the order-size rule
# =============================================================================

def test_contracts_has_no_default_so_nobody_can_pass_a_placeholder():
    """The original bug was `kalshi_fee(p, 1)` standing in for a real ticket.
    Making the argument required is what stops that recurring."""
    import inspect
    sig = inspect.signature(kalshi_fee)
    assert sig.parameters["contracts"].default is inspect.Parameter.empty


def test_per_contract_cost_falls_as_the_order_grows():
    """Because the ceil-to-cent is charged ONCE. At p=0.05 a 1-contract order
    pays 1.00c and a 100-contract order pays 0.34c each - the 2.9x the fee
    notes describe."""
    one = fee_per_contract(0.05, 1)
    hundred = fee_per_contract(0.05, 100)
    assert one == pytest.approx(0.01)
    assert hundred == pytest.approx(0.0034)
    assert one / hundred == pytest.approx(2.94, rel=0.02)


def test_the_old_per_contract_call_overstated_by_about_2x_at_our_prices():
    """Documents the size of the bug being fixed, at the ledger's own prices."""
    for p, lo in ((0.5, 1.05), (0.1, 1.5), (0.05, 2.5)):
        over = fee_per_contract(p, 1) / fee_per_contract(p, 100)
        assert over > lo, (p, over)


def test_zero_or_negative_order_size_is_refused():
    for c in (0, -1, None):
        with pytest.raises(ValueError):
            kalshi_fee(0.5, c)


def test_prices_outside_the_open_unit_interval_are_refused():
    for p in (0.0, 1.0, -0.1, 1.5):
        with pytest.raises(ValueError):
            kalshi_fee(p, 100)


def test_an_unknown_side_is_refused():
    with pytest.raises(ValueError):
        kalshi_fee(0.5, 100, side="both")


# =============================================================================
# edge_after_fees rides on the same arithmetic
# =============================================================================

def test_edge_after_fees_subtracts_the_per_contract_fee():
    fair, price, c = 0.60, 0.50, 100
    raw = fair * (1 - price) - (1 - fair) * price
    assert edge_after_fees(fair, price, c) == pytest.approx(
        raw - fee_per_contract(price, c))


def test_a_fee_free_maker_order_keeps_its_whole_edge():
    fair, price = 0.60, 0.50
    raw = fair * (1 - price) - (1 - fair) * price
    assert edge_after_fees(fair, price, 100, side="maker") == pytest.approx(raw)


def test_a_coin_flip_at_fair_value_loses_exactly_the_fee():
    assert edge_after_fees(0.50, 0.50, 100) == pytest.approx(-0.0175)
