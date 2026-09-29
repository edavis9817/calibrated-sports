"""c-20 research/over_bias_exchange.py - the pure pieces the findings rest on.

Each test discriminates: it asserts one answer on one input AND the other answer
on the neighbouring input, so a function returning a constant cannot pass it.
"""
from research import over_bias_exchange as o

REC = "KXNFLREC-26SEP20CLETB-TBCGODWIN14-5"


def test_taker_fee_is_charged_on_the_whole_order_not_per_contract():
    # 100 NO at 0.55: raw 0.07*100*0.55*0.45 = 1.7325 -> ceil $1.74 -> 1.74c/contract.
    assert abs(o.fee_pp(0.55, 100, "taker", REC) - 1.74) < 1e-9
    # Charged per contract instead, each would ceil 0.017325 to $0.02 = 2.00c.
    assert abs(o.fee_pp(0.55, 1, "taker", REC) - 2.00) < 1e-9


def test_maker_is_fee_free_on_rec_and_charged_on_a_maker_fee_series():
    assert o.fee_pp(0.55, 100, "maker", REC) == 0.0
    assert o.fee_pp(0.55, 100, "maker", "KXNFLSPREAD-26SEP20CLETB-TB3") > 0.0


def test_net_is_payoff_minus_price_minus_fee_on_the_under():
    won = o.net_pp(True, 0.55, 100, "taker", REC)
    lost = o.net_pp(False, 0.55, 100, "taker", REC)
    assert abs(won - (45.0 - 1.74)) < 1e-9
    assert abs(lost - (-55.0 - 1.74)) < 1e-9
    assert abs(o.net_pp(True, 0.55, 100, "maker", REC) - 45.0) < 1e-9


def _p(ts, taker, no_price, size):
    return {"ts": ts, "taker_side": taker, "no_price": no_price,
            "yes_price": round(1 - no_price, 4), "size": size}


def test_a_passive_no_buy_is_filled_by_yes_takers_only():
    yes_takers = [_p(10, "yes", 0.55, 500)]
    no_takers = [_p(10, "no", 0.55, 500)]
    assert o.maker_fill(0, 100, 0.55, 0, yes_takers, 100)["filled"]
    assert not o.maker_fill(0, 100, 0.55, 0, no_takers, 100)["filled"]


def test_a_print_above_our_bid_hits_a_better_bid_and_not_us():
    assert not o.maker_fill(0, 100, 0.55, 0, [_p(10, "yes", 0.56, 500)], 100)["filled"]
    assert o.maker_fill(0, 100, 0.55, 0, [_p(10, "yes", 0.54, 500)], 100)["filled"]


def test_we_sit_behind_the_queue_and_only_prints_before_kickoff_count():
    prints = [_p(10, "yes", 0.55, 150), _p(20, "yes", 0.55, 100)]
    assert not o.maker_fill(0, 100, 0.55, 200, prints, 100)["filled"]      # 250 < 200 + 100
    assert o.maker_fill(0, 100, 0.55, 150, prints, 100)["filled"]          # 250 >= 150 + 100
    assert not o.maker_fill(0, 15, 0.55, 150, prints, 100)["filled"]       # 2nd print after kickoff
    assert o.maker_fill(0, 100, 0.55, 150, prints, 100)["fill_ts"] == 20


def test_two_sided_applies_c19s_staleness_and_crossing_rules():
    assert o.two_sided((0.0, 0.40, 0.42), 600.0)
    assert not o.two_sided((0.0, 0.40, 0.42), 600.1)
    assert not o.two_sided((0.0, 0.42, 0.42), 1.0)
    assert not o.two_sided((0.0, None, 0.42), 1.0)
    assert not o.two_sided(None, 1.0)


def test_floor_cent_keeps_an_exact_cent_and_floors_a_half_cent():
    assert o.floor_cent(0.55) == 0.55           # float noise must not floor it to 0.54
    assert o.floor_cent(0.555) == 0.55
    assert o.floor_cent(1 - 0.445) == 0.55


def test_bias_is_realised_minus_mid_in_pp():
    rows = [{"y": 1.0, "m": 0.6, "game": "a"}, {"y": 0.0, "m": 0.6, "game": "b"}]
    assert abs(o.s_bias(rows) - (-10.0)) < 1e-9
    rows[1]["y"] = 1.0
    assert abs(o.s_bias(rows) - 40.0) < 1e-9


def test_game_block_interval_does_not_narrow_when_rows_are_duplicated_within_a_game():
    rows = [{"y": float(i % 2), "m": 0.5, "game": f"g{i}"} for i in range(20)]
    _, lo1, hi1, g1 = o.boot_games(rows, o.s_bias, draws=500)
    dup = [dict(r) for r in rows for _ in range(20)]
    _, lo2, hi2, g2 = o.boot_games(dup, o.s_bias, draws=500)
    assert g1 == g2 == 20
    assert abs((hi2 - lo2) - (hi1 - lo1)) < 1e-9


def test_verdict_follows_the_preregistered_rule():
    assert o.verdict((-0.1, -1.0, 0.8, 30)) == "RETIRED"
    assert o.verdict((0.0, -1.0, 1.0, 30)) == "RETIRED"
    assert o.verdict((0.5, -0.2, 1.2, 30)).startswith("OPEN")
    assert o.verdict((0.5, 0.1, 1.2, 30)).startswith("SUPPORTED")


def test_conditional_and_gap_use_filled_rows_only():
    rows = [{"game": "a", "MB": {"filled": True, "net": 10.0}},
            {"game": "b", "MB": {"filled": False, "net": 50.0}},
            {"game": "c", "MB": {"filled": True, "net": -4.0}}]
    assert abs(o.s_cond("MB")(rows) - 3.0) < 1e-9
    assert abs(o.s_gap("MB")(rows) - (3.0 - 50.0)) < 1e-9
    assert abs(o.s_per_order("MB")(rows) - 2.0) < 1e-9
    assert abs(o.s_fill("MB")(rows) - 200.0 / 3) < 1e-9
