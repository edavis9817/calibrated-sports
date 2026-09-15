"""Brief 019: the structural tests' arithmetic, on synthetic books.

Run: pytest -q tests/test_structural.py

The failure modes pinned here all produce a plausible number rather than an
error: a mid-based violation nobody could trade, two teams' spread ladders
pooled into one curve, a frozen leg reading as a persistent arbitrage, a no-TD
leg missed so a partial partition looks complete, and a settled market's 0/1
quote read as an untradeable book.
"""
import pytest

from research import structural as st


# =============================================================================
# ladder identity
# =============================================================================

@pytest.mark.parametrize("mid,key", [
    ("KXNFLREC-26SEP13DALNYG-NYGOBECKHAM13-6", "KXNFLREC-26SEP13DALNYG-NYGOBECKHAM13"),
    ("KXNFLRSHATT-26SEP09NESEA-NEDMAYE10-3", "KXNFLRSHATT-26SEP09NESEA-NEDMAYE10"),
    ("KXNFLSPREAD-26SEP13DALNYG-DAL10", "KXNFLSPREAD-26SEP13DALNYG-DAL"),
    ("KXNFLTOTAL-26SEP13DALNYG-28", "KXNFLTOTAL-26SEP13DALNYG"),
    ("KXNFLWINS-27DAL-1", "KXNFLWINS-27DAL"),
    ("KXNFLWINSWEEK-26W12-DAL10", "KXNFLWINSWEEK-26W12-DAL"),
    ("KXNFLWINSTREAK-27-5", "KXNFLWINSTREAK-27"),
])
def test_ladder_key_strips_only_the_threshold(mid, key):
    assert st.ladder_key(mid) == key


def test_the_two_spread_teams_are_separate_ladders():
    """Both teams live in one event on alternating rungs. Pooling them would
    compare Dallas-by-10 with New-York-by-7 as one survival curve."""
    assert st.ladder_key("KXNFLSPREAD-26SEP13DALNYG-DAL10") != \
        st.ladder_key("KXNFLSPREAD-26SEP13DALNYG-NYG10")


def test_a_player_code_with_digits_keeps_them():
    """NYGOBECKHAM13 is a player, 6 is the rung. Only the trailing threshold
    after the last separator is removed."""
    assert st.ladder_key("KXNFLREC-26SEP13DALNYG-NYGOBECKHAM13-6").endswith("BECKHAM13")


# =============================================================================
# the executable condition
# =============================================================================

def test_violation_is_ask_on_the_lower_rung_below_bid_on_the_higher():
    assert st.violates(0.40, 0.45) is True       # buy yes 0.40, buy no 0.55
    assert st.violates(0.45, 0.40) is False
    assert st.violates(0.40, 0.40) is False      # equal is not free money


def test_a_mid_crossing_is_not_a_violation():
    """THE MID TRAP. Mids of 0.42 (lower rung) and 0.44 (higher rung) look
    inverted, but on the touch the lower ask is 0.46 and the higher bid 0.40:
    nothing can be bought for less than it pays."""
    lower_bid, lower_ask = 0.38, 0.46      # mid 0.42
    higher_bid, higher_ask = 0.40, 0.48    # mid 0.44
    assert (lower_bid + lower_ask) / 2 < (higher_bid + higher_ask) / 2
    assert st.violates(lower_ask, higher_bid) is False


def test_a_one_sided_leg_is_never_a_violation():
    assert st.violates(None, 0.9) is False
    assert st.violates(0.1, None) is False


def test_the_payoff_derivation_minimum_is_one():
    """Buy YES on the lower rung and NO on the higher rung. Over every outcome
    region the payoff is at least 1, so cost below 1 is riskless profit."""
    t_lo, t_hi = 3.5, 5.5
    for x in (0, 3, 4, 5, 6, 9):
        yes_lo = 1 if x > t_lo else 0
        no_hi = 1 if not (x > t_hi) else 0
        assert yes_lo + no_hi >= 1


# =============================================================================
# fees
# =============================================================================

def test_net_per_contract_charges_the_fee_on_both_legs():
    from core.fees import fee_per_contract
    net = st.net_per_contract(0.30, 0.35, 100)
    want = 0.05 - fee_per_contract(0.30, 100) - fee_per_contract(0.65, 100)
    assert net == pytest.approx(want)


def test_a_one_cent_violation_at_mid_prices_does_not_survive_fees():
    """Fees peak at 0.5, so a 1c inversion there is eaten twice over."""
    assert st.net_per_contract(0.50, 0.51, 100) < 0
    assert st.net_continuous(0.50, 0.51) < 0


def test_a_wide_violation_at_the_tails_can_survive():
    assert st.net_per_contract(0.05, 0.15, 100) > 0


def test_untradeable_prices_return_none():
    assert st.net_per_contract(0.0, 0.3, 100) is None
    assert st.net_per_contract(0.2, 1.0, 100) is None


# =============================================================================
# episodes and the as-of book
# =============================================================================

def _scan(max_stale=660.0):
    lines = {"KXNFLREC-E-P1-3": 2.5, "KXNFLREC-E-P1-5": 4.5}
    return st.LadderScan("KXNFLREC", "KXNFLREC-E", lines, max_stale=max_stale)


def test_an_episode_opens_extends_and_closes():
    s = _scan()
    s.write(0, "KXNFLREC-E-P1-3", 0.30, 0.40)
    s.write(0, "KXNFLREC-E-P1-5", 0.45, 0.50)      # bid 0.45 > ask 0.40
    s.evaluate(0, {"KXNFLREC-E-P1-3", "KXNFLREC-E-P1-5"})
    s.write(60, "KXNFLREC-E-P1-5", 0.46, 0.50)
    s.evaluate(60, {"KXNFLREC-E-P1-5"})
    s.write(120, "KXNFLREC-E-P1-5", 0.35, 0.50)     # bid falls below ask
    s.evaluate(120, {"KXNFLREC-E-P1-5"})
    eps = s.finish()
    assert len(eps) == 1
    e = eps[0]
    assert (e.start, e.last, e.end, e.n_obs, e.reason) == (0, 60, 120, 2, "closed")
    assert e.lower_s == 60 and e.upper_s == 120
    assert e.gap == pytest.approx(0.05)
    assert s.obs == 2


def test_a_static_leg_is_still_current_between_heartbeats():
    """Write-on-change: a rung that did not move is not rewritten, and its last
    write IS its state. A violation against it is real, not stale."""
    s = _scan()
    s.write(0, "KXNFLREC-E-P1-3", 0.30, 0.40)
    s.write(0, "KXNFLREC-E-P1-5", 0.20, 0.30)
    s.evaluate(0, {"KXNFLREC-E-P1-3", "KXNFLREC-E-P1-5"})
    s.write(200, "KXNFLREC-E-P1-5", 0.45, 0.50)     # lower rung untouched 200s
    s.evaluate(200, {"KXNFLREC-E-P1-5"})
    assert s.obs == 1
    assert list(s.open.values())[0].strict_any is False   # not same-batch


def test_a_frozen_leg_cannot_manufacture_a_persistent_violation():
    """A market whose polling stopped would otherwise hold a violation open
    forever and read as a business rather than as a dead book."""
    s = _scan(max_stale=660.0)
    s.write(0, "KXNFLREC-E-P1-3", 0.30, 0.40)
    s.write(0, "KXNFLREC-E-P1-5", 0.45, 0.50)
    s.evaluate(0, {"KXNFLREC-E-P1-3", "KXNFLREC-E-P1-5"})
    s.write(5000, "KXNFLREC-E-P1-5", 0.46, 0.50)    # lower rung 5000s old
    s.evaluate(5000, {"KXNFLREC-E-P1-5"})
    eps = s.finish()
    assert eps[0].reason == "went stale"
    assert eps[0].last == 0                           # not extended to 5000


def test_an_episode_still_open_at_the_end_is_censored_not_closed():
    s = _scan()
    s.write(0, "KXNFLREC-E-P1-3", 0.30, 0.40)
    s.write(0, "KXNFLREC-E-P1-5", 0.45, 0.50)
    s.evaluate(0, {"KXNFLREC-E-P1-3", "KXNFLREC-E-P1-5"})
    e = s.finish()[0]
    assert e.reason == "censored" and e.upper_s is None


def test_pairs_are_oriented_by_line_not_by_write_order():
    """Touching the HIGHER rung first must still test ask(lower) < bid(higher)."""
    s = _scan()
    s.write(0, "KXNFLREC-E-P1-5", 0.45, 0.50)
    s.write(0, "KXNFLREC-E-P1-3", 0.30, 0.40)
    s.evaluate(0, {"KXNFLREC-E-P1-5", "KXNFLREC-E-P1-3"})
    e = s.finish()[0]
    assert e.lo.endswith("-3") and e.hi.endswith("-5")


def test_rungs_of_different_players_are_never_paired():
    lines = {"KXNFLREC-E-P1-3": 2.5, "KXNFLREC-E-P2-5": 4.5}
    s = st.LadderScan("KXNFLREC", "KXNFLREC-E", lines)
    s.write(0, "KXNFLREC-E-P1-3", 0.30, 0.40)
    s.write(0, "KXNFLREC-E-P2-5", 0.90, 0.95)
    s.evaluate(0, set(lines))
    assert s.finish() == [] and s.obs == 0


def test_persistence_buckets_separate_single_sightings():
    s = _scan()
    s.write(0, "KXNFLREC-E-P1-3", 0.30, 0.40)
    s.write(0, "KXNFLREC-E-P1-5", 0.45, 0.50)
    s.evaluate(0, {"KXNFLREC-E-P1-3", "KXNFLREC-E-P1-5"})
    b = st.persistence_buckets(s.finish())
    assert b["1 seen once"] == 1


# =============================================================================
# partitions
# =============================================================================

def _leg(tk, bid=0.05, ask=0.07, result=None):
    return {"ticker": tk, "yes_bid_dollars": str(bid), "yes_ask_dollars": str(ask),
            "result": result}


def test_a_partition_without_a_no_td_leg_is_incomplete():
    legs = [_leg(f"KXNFLFIRSTTD-26SEP13X-P{i}") for i in range(20)]
    ok, kinds, problems = st.partition_completeness(legs)
    assert ok is False and "no 'no touchdown' outcome" in problems


def test_dst_and_no_td_alone_is_not_a_partition_of_anything():
    """The live week-2 listing: two D/STs and No Touchdown, no players."""
    legs = [_leg("KXNFLFIRSTTD-26SEP17DETBUF-BUFBUFDST"),
            _leg("KXNFLFIRSTTD-26SEP17DETBUF-DETDETDST"),
            _leg("KXNFLFIRSTTD-26SEP17DETBUF-DETNO-TD")]
    ok, kinds, problems = st.partition_completeness(legs)
    assert ok is False
    assert kinds == {"dst": 2, "no-td": 1}


def test_settled_legs_are_not_judged_on_their_zero_one_books():
    """A settled market quotes 0/1. Checking tradeability there would call
    every settled event incomplete for a reason unrelated to the partition."""
    legs = ([_leg(f"KXNFLFIRSTTD-E-P{i}", 0, 1) for i in range(20)]
            + [_leg("KXNFLFIRSTTD-E-XNO-TD", 0, 1)])
    assert st.partition_completeness(legs, check_tradeable=True)[0] is False
    assert st.partition_completeness(legs, check_tradeable=False)[0] is True


def test_the_no_td_match_is_anchored_to_the_end_of_the_ticker():
    """A player code containing the letters must not count as the no-TD leg."""
    assert st.leg_kind("KXNFLFIRSTTD-E-XNO-TD") == "no-td"
    assert st.leg_kind("KXNFLFIRSTTD-E-NONE") == "no-td"
    assert st.leg_kind("KXNFLFIRSTTD-E-LARNONEAL23") == "player"
    assert st.leg_kind("KXNFLFIRSTTD-E-KCKCDST") == "dst"


def test_settlement_with_no_winning_leg_proves_incompleteness():
    legs = [_leg("KXNFLFIRSTTD-E-P1", result="no"), _leg("KXNFLFIRSTTD-E-P2", result="no")]
    assert st.settlement_proof(legs) is False
    legs[0]["result"] = "yes"
    assert st.settlement_proof(legs) is True
    assert st.settlement_proof([_leg("KXNFLFIRSTTD-E-P1")]) is None


# =============================================================================
# catalogue structure
# =============================================================================

def test_classify_distinguishes_ladder_partition_and_standalone():
    events = [("E1", "LAD", 0, 4), ("E2", "PART", 1, 3), ("E3", "ONE", 0, 1)]
    markets = {"E1": ["LAD-E1-P-1", "LAD-E1-P-2", "LAD-E1-P-3", "LAD-E1-P-4"],
               "E2": ["PART-E2-A", "PART-E2-B", "PART-E2-C"],
               "E3": ["ONE-E3-X"]}
    c = st.classify(events, markets)
    assert c["LAD"][0] == "ladder" and c["LAD"][1] == 4
    assert c["PART"][0] == "partition" and c["PART"][2] == 3
    assert c["ONE"][0] == "standalone"


def test_correlation_sign():
    assert st.correlation([1, 2, 3], [1, 2, 3]) == pytest.approx(1.0)
    assert st.correlation([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)


def test_the_subtitle_is_the_authority_over_the_ticker():
    """SF@LA listed `-LARNONE` as a second "No Touchdown" leg. The subtitle
    classifies it regardless of what the ticker happens to look like."""
    leg = {"ticker": "KXNFLFIRSTTD-26SEP10SFLAR-LARNONE",
           "yes_sub_title": "No Touchdown"}
    assert st.leg_kind(leg) == "no-td"
    assert st.leg_kind({"ticker": "KXNFLFIRSTTD-E-XNONEZ",
                        "yes_sub_title": "Nonezz Player"}) == "player"
    assert st.leg_kind({"ticker": "KXNFLFIRSTTD-E-KCKCDST",
                        "yes_sub_title": "KC Chiefs D/ST"}) == "dst"


def test_two_no_touchdown_legs_are_not_a_partition():
    """The same outcome listed twice: if nobody scores, both pay."""
    legs = ([_leg(f"KXNFLFIRSTTD-E-P{i}") for i in range(20)]
            + [{"ticker": "KXNFLFIRSTTD-E-NONE", "yes_sub_title": "No Touchdown",
                "yes_bid_dollars": "0.02", "yes_ask_dollars": "0.04"},
               {"ticker": "KXNFLFIRSTTD-E-LARNONE", "yes_sub_title": "No Touchdown",
                "yes_bid_dollars": "0.02", "yes_ask_dollars": "0.04"}])
    ok, kinds, problems = st.partition_completeness(legs)
    assert ok is False
    assert kinds["no-td"] == 2
    assert any("listed twice" in p for p in problems)


def test_completeness_reads_the_whole_leg_not_just_the_ticker():
    """The classifier must be given the market dict, or the subtitle it relies
    on is never seen."""
    import inspect
    src = inspect.getsource(st.partition_completeness)
    code = "\n".join(l.split("#", 1)[0] for l in src.splitlines())
    assert "leg_kind(m)" in code
