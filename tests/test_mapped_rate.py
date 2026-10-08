"""The mapped-rate check in the staleness gate (a-68), driven to every answer.

The failure it exists for: 1,589 week-4 props were listed and 87 carried an
outcome at kickoff, and nothing was red, because a market the mapper has not
looked at has no row and so appears in no census of reasons. Every test builds
its own store under tmp_path; none opens a configured one.
"""
import sqlite3

from analytics import staleness as st

DAY = 86400
# Week 4 of 2026: first kickoff Fri 2026-10-02 00:15Z (the Thursday night
# game), last Tue 2026-10-06 00:15Z (Monday night).
FIRST = 1790900100.0
LAST = FIRST + 4 * DAY
SUNDAY = FIRST + 2 * DAY + 17 * 3600        # Sunday 17:15Z, the early slate


def store(tmp_path, markets, mapped=(), refused=(), weeks=((4, FIRST, LAST),)):
    path = str(tmp_path / "market_log.db")
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE nfl_games (sport, game_id, season, week, kickoff_ts);
        CREATE TABLE markets (venue, market_id, market_type, first_seen);
        CREATE TABLE market_outcome (venue, market_id, outcome_id, unmapped_reason);
    """)
    for w, a, b in weeks:
        c.executemany("INSERT INTO nfl_games VALUES ('nfl',?,2026,?,?)",
                      [("g%da" % w, w, a), ("g%db" % w, w, b)])
    c.executemany("INSERT INTO markets VALUES ('kalshi',?,'prop',?)", markets)
    c.executemany("INSERT INTO market_outcome VALUES ('kalshi',?,?,NULL)",
                  [(m, "o%d" % i) for i, m in enumerate(mapped)])
    c.executemany("INSERT INTO market_outcome VALUES ('kalshi',?,NULL,?)", refused)
    c.commit()
    c.close()
    return {"market_log.db": path}


def rungs(series, n, day="26OCT04", seen=SUNDAY - 3 * DAY):
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
    assert r.status == st.RED and r.detail["n"] == 100 and r.detail["mapped"] == 6


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
    assert {i.key for i in broken.failing} == {REC, RSH}
