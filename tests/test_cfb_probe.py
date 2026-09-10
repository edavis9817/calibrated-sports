"""The CFB classifier. Run: pytest -q

The probe is disposable; this test is not. It pins the one property that makes
running two football captures against one venue safe: the NFL filter and the
CFB filter must never both accept the same series. If they overlap, whichever
process discovers a market second silently double-polls it, and on Kalshi that
is the shared rate limit the NFL slate depends on.
"""
import cfb_probe
from venues.kalshi import is_football_series

CFB = cfb_probe.is_cfb_series


def test_the_nfl_and_cfb_filters_are_mutually_exclusive():
    """Measured live 2026-09-10: 137 NCAAF series, and the NFL filter accepts
    exactly 0 of them. That is not luck - the NFL filter excludes NCAA on
    purpose, to keep KXNCAAFCONFLEAVE (CO-NFL-EAVE) out."""
    series = [
        ("KXNCAAFGAME-26SEP12", "College Football Game Winner"),
        ("KXNCAAFSPREAD-26SEP12", "College Football Spread"),
        ("KXNCAAFCONFLEAVE", "College Football Conference Leave"),
        ("KXNCAAFCOACHLEAVE", "College Football Coach Out By"),
        ("KXNFLGAME-26SEP13SEA", "Pro Football Game Winner"),
        ("KXNFLWINS-MIA", "Pro Football Team Wins"),
    ]
    for ticker, title in series:
        nfl = is_football_series(ticker, title, "Sports")
        cfb = CFB(ticker, title, "Sports")
        assert not (nfl and cfb), f"{ticker} accepted by BOTH filters"
        assert nfl or cfb, f"{ticker} accepted by NEITHER filter"


def test_the_f_in_ncaaf_is_load_bearing():
    """KXNCAAMBB / KXNCAAMBS / KXNCAAMWR are basketball, baseball and
    wrestling. A bare NCAA match pulls in 151 series that are not football:
    288 NCAA-or-COLLEGE series against 137 with NCAAF."""
    for ticker, title in (("KXNCAAMBB1", "NCAA Basketball Champion"),
                          ("KXNCAAMBS", "College Baseball"),
                          ("KXNCAAMWR", "College Wrestling"),
                          ("KXNCAAMBAGAME", "College Baseball Game")):
        assert not CFB(ticker, title, "Sports"), f"{ticker} is not football"
    assert CFB("KXNCAAFGAME", "College Football Game Winner", "Sports")


def test_the_category_guard_holds():
    """Sports alone is 3,755 series. An NCAAF substring without the category
    guard trusts a ticker scheme to stay a ticker scheme."""
    assert not CFB("KXNCAAFGAME", "College Football", "Economics")
    assert not CFB("KXNCAAFGAME", "College Football", "")
    assert CFB("KXNCAAFGAME", "College Football", "sports")   # case-insensitive


def test_a_title_naming_another_sport_is_rejected_even_with_an_ncaaf_ticker():
    """Currently excludes nothing - zero NCAAF series name another sport - and
    is here because the ticker scheme is Kalshi's to change."""
    assert not CFB("KXNCAAFXYZ", "College Basketball Champion", "Sports")
    assert CFB("KXNCAAFXYZ", "College Football Champion", "Sports")


def test_a_college_football_title_is_enough_without_the_ticker():
    """Kalshi renames tickers more often than it renames the sport."""
    assert CFB("KXSOMETHINGELSE", "College Football Game Winner", "Sports")


def test_futures_are_tiered_apart_from_game_markets():
    """Only used to pick a cadence, but a season future polled at 60s for two
    days is 2,880 identical rows."""
    assert cfb_probe.classify("KXNCAAFCHAMP", "National Champion") == "future"
    assert cfb_probe.classify("KXNCAAFWINS", "Team Wins") == "future"
    assert cfb_probe.classify("KXNCAAFHEISMAN", "Heisman Winner") == "future"
    assert cfb_probe.classify("KXNCAAFSPREAD", "Spread") == "spread"
    assert cfb_probe.classify("KXNCAAFTOTAL", "Total Points") == "total"


def test_tiering_never_puts_a_future_on_the_hot_tier():
    import time
    now = time.time()
    fut = {"market_type": "future", "close_ts": now + 600}
    assert cfb_probe.tier_for(fut, now) == "futures"
    game = {"market_type": "game", "close_ts": now + 600}
    assert cfb_probe.tier_for(game, now) == "hot"
    far = {"market_type": "game", "close_ts": now + 5 * 3600}
    assert cfb_probe.tier_for(far, now) == "game"
    assert cfb_probe.tier_for({"market_type": "game"}, now) == "game"
