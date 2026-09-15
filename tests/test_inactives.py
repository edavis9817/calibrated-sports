"""Brief 023 Part 3: the proxy's pure pieces."""
from research import inactives as I


def test_classify_follows_the_preregistered_rule_and_flags_who_played():
    assert I.classify(0, True, True)["inactive_by_rule"]
    assert I.classify(None, False, False)["inactive_by_rule"]
    c = I.classify(32, True, False)
    assert c["inactive_by_rule"] and c["played"] and "no player-week row" in c["reasons"]
    assert not I.classify(40, True, True)["inactive_by_rule"]


def test_team_from_ticker_resolves_kalshi_codes_to_the_games_teams():
    assert I.team_from_ticker("KXNFLREC-26SEP13DALNYG-NYGOBECKHAM13-2", {"DAL", "NYG"}) == "NYG"
    assert I.team_from_ticker("KXNFLREC-26SEP13CLEJAC-JACBTHOMAS1-4", {"CLE", "JAX"}) == "JAX"
    assert I.team_from_ticker("KXNFLREC-26SEP13MIALV-LVBBOWERS89-3", {"MIA", "LV"}) == "LV"


def test_t0_collapse_uses_the_highest_priced_rung():
    rungs = {"r2": [(100, 0.40, 0.42), (200, 0.05, 0.07)],
             "r3": [(100, 0.20, 0.22), (200, 0.02, 0.04)]}
    assert I.find_t0(rungs, 50, 1000) == (200, "collapse")


def test_t0_does_not_fire_while_any_rung_is_above_the_floor():
    rungs = {"r2": [(100, 0.40, 0.42), (200, 0.30, 0.32)],
             "r3": [(100, 0.02, 0.04), (200, 0.02, 0.04)]}
    t, why = I.find_t0(rungs, 50, 250)
    assert t is None


def test_t0_stopped_quoting_and_never_live():
    rungs = {"r2": [(100, 0.40, 0.42)]}
    assert I.find_t0(rungs, 50, 2000) == (100 + I.MAX_STALE, "stopped quoting")
    assert I.find_t0({"r2": [(10, 0.4, 0.42)]}, 5000, 9000)[1] == "never had a live market near kickoff"


def test_time_to_level():
    s = [(100, 0.30, 0.32), (160, 0.36, 0.38), (400, 0.40, 0.42)]
    assert I.time_to_level(s, 100, 0.41, 1800) == 300
    assert I.time_to_level(s, 100, 0.90, 1800) is None
