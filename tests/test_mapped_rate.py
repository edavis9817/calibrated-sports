"""The mapped-rate check in the staleness gate (a-68, a-73), driven to every answer.

The failure it exists for: 1,589 week-4 props were listed and 87 carried an
outcome at kickoff, and nothing was red, because a market the mapper has not
looked at has no row and so appears in no census of reasons. And the reading
that hid it for four weeks: by the Tuesday after, every one of them WAS mapped,
so the rate is measured before each market's own kickoff, never at any time.
Every test builds its own store under tmp_path; none opens a configured one.
"""
import sqlite3

from analytics import staleness as st

DAY = 86400
# Week 4 of 2026: first kickoff Fri 2026-10-02 00:15Z (the Thursday night
# game), last Tue 2026-10-06 00:15Z (Monday night).
FIRST = 1790900100.0
LAST = FIRST + 4 * DAY
SUNDAY = FIRST + 2 * DAY + 17 * 3600        # Sunday 17:15Z, the early slate
SUN_KICK = FIRST + 2 * DAY + 20 * 3600 + 600  # Sunday 20:25Z, the late window
LISTED = SUNDAY - 3 * DAY                   # Thursday 17:15Z, the listing hour


def store(tmp_path, markets, mapped=(), refused=(), weeks=((4, FIRST, LAST),)):
    """Three games a week, every one DAL at NYG on its own day: the Thursday
    night game (ticker date 26OCT01, kickoff Friday 00:15Z), a Sunday 20:25Z
    game (26OCT04) and Monday night (26OCT05). `mapped` is market ids, or
    (market id, the time its outcome was created); the default is a minute
    after listing, which is in time for every game."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = str(tmp_path / "market_log.db")
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE nfl_games (sport, game_id, season, week, gameday, home_team,
                                away_team, kickoff_ts);
        CREATE TABLE markets (venue, market_id, market_type, first_seen);
        CREATE TABLE market_outcome (venue, market_id, outcome_id, unmapped_reason);
        CREATE TABLE outcomes (outcome_id, created_ts);
    """)
    for w, a, b in weeks:
        for tag, kick in (("thu", a), ("sun", a + (SUN_KICK - FIRST)), ("mon", b)):
            c.execute("INSERT INTO nfl_games VALUES ('nfl',?,2026,?,?,'NYG','DAL',?)",
                      ("g%d%s" % (w, tag), w, gameday(kick), kick))
    c.executemany("INSERT INTO markets VALUES ('kalshi',?,'prop',?)", markets)
    seen = dict(markets)
    for i, m in enumerate(mapped):
        mid, created = m if isinstance(m, tuple) else (m, seen[m] + 60)
        c.execute("INSERT INTO market_outcome VALUES ('kalshi',?,?,NULL)", (mid, "o%d" % i))
        c.execute("INSERT INTO outcomes VALUES (?,?)", ("o%d" % i, created))
    c.executemany("INSERT INTO market_outcome VALUES ('kalshi',?,NULL,?)", refused)
    c.commit()
    c.close()
    return {"market_log.db": path}


def gameday(kick):
    """The game's local (Eastern) day, which is what a ticker carries."""
    import datetime as dt
    return dt.datetime.fromtimestamp(kick - 4 * 3600, dt.timezone.utc).strftime("%Y-%m-%d")


def rungs(series, n, day="26OCT04", seen=LISTED):
    return [("%s-%sDALNYG-P%d" % (series, day, i), seen) for i in range(n)]


def item(items, key):
    got = [i for i in items if i.key == key]
    assert len(got) == 1, [i.key for i in items]
    return got[0]


REC, RSH = "mapped_rate:kalshi:KXNFLREC", "mapped_rate:kalshi:KXNFLRSHATT"


def test_the_fixture_dates_are_the_week_they_claim():
    import datetime as dt
    assert dt.datetime.fromtimestamp(FIRST, dt.timezone.utc).strftime("%a %Y-%m-%d %H:%M") \
        == "Fri 2026-10-02 00:15"
    assert dt.datetime.fromtimestamp(SUNDAY, dt.timezone.utc).strftime("%a %H:%M") == "Sun 17:15"
    assert [gameday(k) for k in (FIRST, SUN_KICK, LAST)] == ["2026-10-01", "2026-10-04", "2026-10-05"]
    assert SUNDAY + 3600 < SUN_KICK          # the Sunday game is still ahead in every test at SUNDAY


def test_week_four_as_it_was_at_kickoff_is_red(tmp_path):
    """The real shape: 87 of 1,589 mapped, the rest never looked at."""
    rec, rsh = rungs("KXNFLREC", 1407), rungs("KXNFLRSHATT", 182)
    mapped = [m for m, _ in rec[:80]] + [m for m, _ in rsh[:7]]
    out = st.check_mapped_rate(store(tmp_path, rec + rsh, mapped), SUNDAY)
    r = item(out, REC)
    assert r.status == st.RED
    assert "80 of 1407" in r.statement and "1327 never looked at, 0 refused" in r.statement
    assert "no market_outcome row" in r.statement
    assert r.detail["rate"] == round(80 / 1407, 4)
    assert item(out, RSH).status == st.RED


def test_the_same_week_mapped_is_ok_with_the_count_in_the_statement(tmp_path):
    rec, rsh = rungs("KXNFLREC", 1407), rungs("KXNFLRSHATT", 182)
    out = st.check_mapped_rate(store(tmp_path, rec + rsh, [m for m, _ in rec + rsh]), SUNDAY)
    assert item(out, REC).status == st.OK and "1407 of 1407" in item(out, REC).statement
    assert item(out, RSH).status == st.OK


def test_the_floor_is_the_line_and_one_market_crosses_it(tmp_path):
    rec = rungs("KXNFLREC", 100)
    rsh = rungs("KXNFLRSHATT", 100)
    s = store(tmp_path, rec + rsh, [m for m, _ in rec[:95]] + [m for m, _ in rsh[:94]])
    out = st.check_mapped_rate(s, SUNDAY)
    assert item(out, REC).status == st.OK          # 0.95 is at the floor
    assert item(out, RSH).status == st.RED         # 0.94 is under it


def test_a_refusal_counts_against_the_rate_and_is_told_apart(tmp_path):
    """A row with a reason is the mapper refusing; no row is the mapper not
    having run. Both are unmapped; they are different defects."""
    rec = rungs("KXNFLREC", 10)
    rsh = rungs("KXNFLRSHATT", 1)
    s = store(tmp_path, rec + rsh, [m for m, _ in rec[:5]] + [rsh[0][0]],
              refused=[(m, "no player matches 'Hollywood Brown'") for m, _ in rec[5:9]])
    r = item(st.check_mapped_rate(s, SUNDAY), REC)
    assert r.status == st.RED
    assert "1 never looked at, 4 refused" in r.statement
    assert "no player matches 'Hollywood Brown' (4)" in r.statement


def test_a_rung_listed_a_minute_ago_is_not_yet_counted_but_an_old_one_is(tmp_path):
    old = rungs("KXNFLREC", 20)
    young = [("KXNFLREC-26OCT04DALNYG-NEW%d" % i, SUNDAY - 60) for i in range(50)]
    s = store(tmp_path, old + young + rungs("KXNFLRSHATT", 1), [m for m, _ in old])
    r = item(st.check_mapped_rate(s, SUNDAY), REC)
    assert r.status == st.OK and r.detail["n"] == 20 and r.detail["young"] == 50
    # the same fifty an hour later, still unmapped, are a failure
    r = item(st.check_mapped_rate(s, SUNDAY + 3600), REC)
    assert r.status == st.RED and r.detail["n"] == 70


def test_another_weeks_markets_are_not_in_this_weeks_rate(tmp_path):
    """Last week fully mapped must not dilute this week's collapse."""
    last = rungs("KXNFLREC", 1500, day="26SEP27")
    this = rungs("KXNFLREC", 100)
    s = store(tmp_path, last + this + rungs("KXNFLRSHATT", 1),
              [m for m, _ in last] + [m for m, _ in this[:6]],
              weeks=((3, FIRST - 7 * DAY, LAST - 7 * DAY), (4, FIRST, LAST)))
    r = item(st.check_mapped_rate(s, SUNDAY), REC)
    assert r.status == st.RED and r.detail["n"] == 100 and r.detail["in_time"] == 6


def test_the_thursday_night_ticker_date_is_inside_its_own_week(tmp_path):
    """A 00:15Z Friday kickoff carries Thursday's date in the ticker."""
    thu = rungs("KXNFLREC", 5, day="26OCT01")
    r = item(st.check_mapped_rate(store(tmp_path, thu, []), SUNDAY), REC)
    assert r.detail["n"] == 5 and r.status == st.RED


def test_nothing_listed_is_no_information_early_and_red_by_kickoff(tmp_path):
    s = store(tmp_path, [])
    early = item(st.check_mapped_rate(s, FIRST - 2 * DAY), REC)
    assert early.status == st.SKIPPED and "not a pass" in early.statement
    late = item(st.check_mapped_rate(s, FIRST - 3600), REC)
    assert late.status == st.RED and "discovery failure" in late.statement


def test_a_ticker_with_no_readable_date_is_red_not_dropped(tmp_path):
    good = rungs("KXNFLREC", 3)
    s = store(tmp_path, good + [("KXNFLREC-NODATE-P1", SUNDAY - DAY)],
              [m for m, _ in good])
    out = st.check_mapped_rate(s, SUNDAY)
    assert item(out, "mapped_rate:undated").status == st.RED
    assert item(out, REC).status == st.OK


def test_no_week_ahead_skips_out_loud(tmp_path):
    out = st.check_mapped_rate(store(tmp_path, rungs("KXNFLREC", 3)), LAST + 30 * DAY)
    assert [i.status for i in out] == [st.SKIPPED]


def test_the_gate_runs_it_and_a_collapse_fails_the_gate(tmp_path):
    """Wired into `run`, not merely defined: the whole gate goes from clean to
    failing on this one change."""
    from tests.test_staleness import NOW, gate
    clean = gate(tmp_path / "a", nfl_weather_kick=NOW)
    assert clean.clean, [i.statement for i in clean.failing]
    assert item(clean.items, REC).status == st.OK
    broken = gate(tmp_path / "b", nfl_weather_kick=NOW, props_mapped=1)
    assert not broken.clean
    assert {i.key for i in broken.failing} == {REC, RSH, "unexamined:kalshi"}


# --- before kickoff, not at any time (a-73) -----------------------------------

TUESDAY = LAST + 13 * 3600                   # Tuesday 13:15Z: the weekly pass has just run
PREV = "mapped_rate:kalshi:KXNFLREC:2026w04"


def test_mapped_after_its_own_kickoff_is_not_mapped_in_time(tmp_path):
    """The whole defect in one market: an outcome that exists NOW, created
    after the game. `mapped at any time` reads 1.000 here."""
    thu = rungs("KXNFLREC", 20, day="26OCT01", seen=FIRST - 7 * 3600)
    on_time = [(m, FIRST - 3600) for m, _ in thu[:1]]
    late = [(m, FIRST + 4 * DAY) for m, _ in thu[1:]]
    r = item(st.check_mapped_rate(store(tmp_path, thu, on_time + late), SUNDAY), REC)
    assert r.status == st.RED
    assert "1 of 20" in r.statement and "19 mapped only after kickoff, 0 never looked at" in r.statement
    assert r.detail["late"] == 19 and r.detail["no_row"] == 0
    # and the discriminating half: the same twenty created an hour before kickoff are fine
    ok = store(tmp_path / "ok", thu, [(m, FIRST - 3600) for m, _ in thu])
    assert item(st.check_mapped_rate(ok, SUNDAY), REC).status == st.OK


def test_the_instant_of_kickoff_is_the_line(tmp_path):
    thu = rungs("KXNFLREC", 2, day="26OCT01", seen=FIRST - 7 * 3600)
    s = store(tmp_path, thu, [(thu[0][0], FIRST - 1), (thu[1][0], FIRST)])
    r = item(st.check_mapped_rate(s, SUNDAY), REC)
    assert r.detail["in_time"] == 1 and r.detail["late"] == 1


def test_the_tuesday_after_reads_last_week_red_though_every_market_is_mapped(tmp_path):
    """Weeks 2 and 4 as a-67 read them: the week has rolled, the full pass has
    mapped everything, and a census of what is mapped NOW is spotless."""
    wk4 = rungs("KXNFLREC", 1407) + rungs("KXNFLRSHATT", 182)
    wk5 = rungs("KXNFLREC", 90, day="26OCT11", seen=TUESDAY - 3600) \
        + rungs("KXNFLRSHATT", 9, day="26OCT11", seen=TUESDAY - 3600)
    mapped = [(m, SUN_KICK - 3600) for m, _ in wk4[:87]] \
        + [(m, TUESDAY - 600) for m, _ in wk4[87:]] + [(m, TUESDAY - 600) for m, _ in wk5]
    s = store(tmp_path, wk4 + wk5, mapped, weeks=((4, FIRST, LAST), (5, FIRST + 7 * DAY, LAST + 7 * DAY)))
    out = st.check_mapped_rate(s, TUESDAY)
    assert item(out, REC).status == st.OK and "2026 week 5" in item(out, REC).statement
    prev = item(out, PREV)
    assert prev.status == st.RED
    assert "2026 week 4: 87 of 1407" in prev.statement
    assert "1320 mapped only after kickoff" in prev.statement
    assert "week 4 is played" in prev.statement and prev.detail["pricing"] is False
    assert item(out, "mapped_rate:kalshi:KXNFLRSHATT:2026w04").status == st.RED


def test_last_week_mapped_in_time_is_ok_and_drops_out_after_eight_days(tmp_path):
    wk4 = rungs("KXNFLREC", 30) + rungs("KXNFLRSHATT", 3)
    s = store(tmp_path, wk4, [m for m, _ in wk4],
              weeks=((4, FIRST, LAST), (5, FIRST + 7 * DAY, LAST + 7 * DAY),
                     (6, FIRST + 14 * DAY, LAST + 14 * DAY)))
    assert item(st.check_mapped_rate(s, TUESDAY), PREV).status == st.OK
    later = st.check_mapped_rate(s, LAST + st.PREVIOUS_WEEK_WITHIN_S + 3600)
    assert PREV not in {i.key for i in later}


def test_a_rung_listed_after_kickoff_could_not_have_been_mapped_in_time(tmp_path):
    """In-game listings are out of the denominator and counted, not failed."""
    pre = rungs("KXNFLREC", 10, day="26OCT01", seen=FIRST - 7 * 3600)
    ingame = [("KXNFLREC-26OCT01DALNYG-LIVE%d" % i, FIRST + 1800) for i in range(40)]
    s = store(tmp_path, pre + ingame, [m for m, _ in pre] + [(m, FIRST + 2400) for m, _ in ingame])
    r = item(st.check_mapped_rate(s, SUNDAY), REC)
    assert r.status == st.OK and r.detail["n"] == 10 and r.detail["listed_late"] == 40


def test_a_ticker_that_matches_no_game_is_red_not_dropped(tmp_path):
    """An unmapped market gets its kickoff from the schedule. One the schedule
    cannot place has no kickoff, and silently skipping it would let the guard
    pass over exactly the markets it could not read."""
    good = rungs("KXNFLREC", 3)
    s = store(tmp_path, good + [("KXNFLREC-26OCT04DALPHI-P1", LISTED)], [m for m, _ in good])
    out = st.check_mapped_rate(s, SUNDAY)
    u = item(out, "mapped_rate:unplaced")
    assert u.status == st.RED and "KXNFLREC-26OCT04DALPHI-P1" in u.statement
    assert "mapped_rate:unplaced" not in {i.key for i in st.check_mapped_rate(
        store(tmp_path / "ok", good, [m for m, _ in good]), SUNDAY)}


def test_kalshis_spelling_of_a_team_places_in_the_nflverse_game(tmp_path):
    """Kalshi writes JAC and LAR; the schedule says JAX and LA."""
    s = store(tmp_path, [])
    c = sqlite3.connect(s["market_log.db"])
    c.execute("UPDATE nfl_games SET home_team='JAX', away_team='LA' WHERE game_id='g4sun'")
    c.execute("INSERT INTO markets VALUES ('kalshi','KXNFLREC-26OCT04LARJAC-P1','prop',?)", (LISTED,))
    c.commit()
    c.close()
    out = st.check_mapped_rate(s, SUNDAY)
    assert "mapped_rate:unplaced" not in {i.key for i in out}
    assert item(out, REC).detail["n"] == 1


# --- the unexamined state (a-73) ----------------------------------------------

def test_a_market_nobody_looked_at_is_red_and_told_apart_from_a_refusal(tmp_path):
    rec = rungs("KXNFLREC", 10)
    s = store(tmp_path, rec, [rec[0][0]],
              refused=[(m, "no player matches 'Hollywood Brown'") for m, _ in rec[1:4]])
    r = item(st.check_unexamined(s, SUNDAY), "unexamined:kalshi")
    assert r.status == st.RED
    assert r.statement.startswith("6 of 10 kalshi markets have NO market_outcome row")
    assert r.detail["unexamined"] == 6 and r.detail["oldest"] == LISTED


def test_every_market_examined_is_ok_even_when_every_one_was_refused(tmp_path):
    """Refused is a different defect and another check's business: this one
    asks only whether the mapper LOOKED."""
    rec = rungs("KXNFLREC", 5)
    s = store(tmp_path, rec, refused=[(m, "no such player") for m, _ in rec])
    r = item(st.check_unexamined(s, SUNDAY), "unexamined:kalshi")
    assert r.status == st.OK and r.detail["unexamined"] == 0


def test_a_market_listed_inside_the_grace_is_not_yet_unexamined(tmp_path):
    rec = rungs("KXNFLREC", 5, seen=SUNDAY - 60)
    s = store(tmp_path, rec)
    assert item(st.check_unexamined(s, SUNDAY), "unexamined:kalshi").status == st.OK
    assert item(st.check_unexamined(s, SUNDAY + 3600), "unexamined:kalshi").status == st.RED


def test_unexamined_is_per_venue_and_a_books_markets_are_one_venue(tmp_path):
    s = store(tmp_path, rungs("KXNFLREC", 2), [m for m, _ in rungs("KXNFLREC", 2)])
    c = sqlite3.connect(s["market_log.db"])
    c.executemany("INSERT INTO markets VALUES (?,?,'prop',?)",
                  [("polymarket", "0xa", LISTED), ("oddsapi:draftkings", "q1", LISTED),
                   ("oddsapi:fanduel", "q2", LISTED)])
    c.execute("INSERT INTO market_outcome VALUES ('oddsapi:fanduel','q2',NULL,'book-only claim x')")
    c.commit()
    c.close()
    out = st.check_unexamined(s, SUNDAY)
    assert {i.key: i.status for i in out} == {
        "unexamined:kalshi": st.OK, "unexamined:polymarket": st.RED, "unexamined:oddsapi": st.RED}
    assert item(out, "unexamined:oddsapi").detail == {
        "listed": 2, "unexamined": 1, "stale": 1, "oldest": LISTED}


def test_an_empty_markets_table_is_no_information_not_a_pass(tmp_path):
    out = st.check_unexamined(store(tmp_path, []), SUNDAY)
    assert [i.status for i in out] == [st.SKIPPED]
