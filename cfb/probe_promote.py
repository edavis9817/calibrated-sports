"""Promote the CFB exchange probe capture into cfb.db: markets and closes.

`cfb_probe.db` (2026-09-10 19:41Z to 09-13 03:34Z) is the only exchange record
college football has - 47,553 Kalshi and Polymarket markets and 50.6M quote
rows from `cfb_probe.py`. It is opened READ-ONLY and never written.

WHAT IS PROMOTED
  cfb_exchange_markets   every market, with the game it belongs to when the join
                         finds one (and why not when it does not)
  cfb_exchange_closes    for every market whose game kicked off inside the
                         capture window: the last quote STRICTLY BEFORE kickoff
WHAT STAYS IN THE ARCHIVE
  the 50.6M-row quote history, poll_log, the probe's own manifest and cfbd_games,
  and every quote after kickoff. The raw shards are in cfb/raw_archive.

PROVENANCE IS ON EVERY ROW (`capture`) AND IN `cfb_limitations`. This was a
probe, not the logger: its game-tier poll took a median 40.9s and up to 111.9s,
so the loop ran saturated; a second copy of the process ran from 2026-09-11
16:00Z and doubled the poll rate; its `sport` column says 'nfl' on every CFB
row. A close from it is a real price at a real instant, but the instant can be
up to a couple of minutes before kickoff, and `age_s` says exactly how far.

THE CLOSE IS STRICTLY BEFORE KICKOFF. research/cfb_calibration.py (C01) used
"at or before"; the NFL CLV work uses strictly before, because a quote stamped
at the kickoff second may already be in-play. The two differ only where a quote
lands on the kickoff second, and that count is measured here
(`probe.closes_quote_at_kickoff_second`), not assumed to be zero.

KICKOFF AND THE JOIN COME FROM THE FULL-SEASON SCHEDULE (`cfb_games`), because
the probe listed markets for weeks CFBD has not been asked for yet. C01 keyed
on CFBD's `start_ts`; on the probe weekend the two agree on all 303 games to
the second, and that agreement is re-measured on every run
(`probe.kickoff_agreement_with_cfbd`). The join is C01's: date +/-1 day and
BOTH team names after normalisation, and a unique candidate or no match at all.
"""
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from statistics import median
from zoneinfo import ZoneInfo

import config

ET = ZoneInfo("America/New_York")
CAPTURE = "probe:cfb_probe.py 2026-09-10..13, non-production cadence"
PART = "probe_2026-09-10_13"

# C01's alias table and normaliser, copied rather than imported so the ingest
# does not depend on a research script. `tests/test_probe_promote.py` asserts
# they still agree with research/cfb_calibration.py.
ALIAS = {
    "umass": "massachusetts",
    "se louisiana": "southeastern louisiana",
    "ul monroe": "louisiana monroe",
    "ut martin": "tennessee martin",
    "long island university": "liu",
    "ualbany": "albany",
    "university at albany": "albany",
    "nicholls": "nicholls state",
    "ut rio grande valley": "utrgv",
    "nc state": "north carolina state",
    "app state": "appalachian state",
    "southern": "southern university",
    "grambling": "grambling state",
    "central connecticut": "central connecticut state",
    "texas am": "texas aandm",
    "miami fl": "miami",
}


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    s = s.lower().strip().replace("&", "and")
    s = re.sub(r"[()]", " ", s)
    s = re.sub(r"[-/.]", " ", s)
    s = re.sub(r"\bst\b", "state", s)
    s = re.sub(r"[^a-z0-9 ]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return ALIAS.get(s, s)


KALSHI_DATE = re.compile(r"^(\d{2})([A-Z]{3})(\d{2})")
POLY_GAME = re.compile(r"^cfb-.+-(\d{4}-\d{2}-\d{2})$")
POLY_TITLE = re.compile(r"^(?P<a>.+?)\s+vs\.?\s+(?P<b>.+?)(?::|$)")


def probe_path():
    return config.storage_path("cfb_probe.db")


def open_probe():
    import sqlite3
    return sqlite3.connect(f"file:{probe_path()}?mode=ro", uri=True)


def kalshi_game_key(event_id):
    return event_id.split("-", 1)[1] if event_id and "-" in event_id else None


def _games_by_day(games):
    by_day = defaultdict(list)
    for g in games:
        day = datetime.fromtimestamp(g["start_ts"], ET).date()
        by_day[day].append(g)
    return by_day


def _find(by_day, day, names):
    """Unique game on day +/-1 whose two normalised names are `names`."""
    for k in (0, -1, 1):
        cand = [g for g in by_day.get(day + timedelta(days=k), [])
                if {g["nh"], g["na"]} == names]
        if cand:
            return cand if len(cand) > 1 else cand[0]
    return None


def match_games(probe, games):
    """{(venue, event_id): (game_id or None, note)} for every probe event."""
    by_day = _games_by_day(games)
    out = {}

    # Kalshi: the two-legged game-winner series names both teams for a key
    # shared by every series of that game.
    legs = defaultdict(set)
    for mid, subj in probe.execute(
            "SELECT market_id, subject FROM markets WHERE market_id LIKE 'KXNCAAFGAME-%'"):
        p = mid.split("-")
        if len(p) >= 3:
            legs[p[1]].add(norm(subj))
    for (ev,) in probe.execute("SELECT DISTINCT event_id FROM markets WHERE venue='cfb_kalshi'"):
        gkey = kalshi_game_key(ev)
        m = KALSHI_DATE.match(gkey or "")
        if not m:
            out[("cfb_kalshi", ev)] = (None, "not a game (no date code)")
            continue
        names = legs.get(gkey)
        if not names or len(names) != 2:
            out[("cfb_kalshi", ev)] = (None, "no two-legged game-winner market names the teams")
            continue
        try:
            day = datetime.strptime(m.group(0), "%y%b%d").date()
        except ValueError:
            out[("cfb_kalshi", ev)] = (None, "unparseable date code")
            continue
        g = _find(by_day, day, names)
        out[("cfb_kalshi", ev)] = _result(g, names)

    # Polymarket: game events are slugged cfb-<a>-<b>-YYYY-MM-DD, and their
    # titles carry "A vs. B: ...".
    titles = defaultdict(Counter)
    for ev, title in probe.execute(
            "SELECT event_id, title FROM markets WHERE venue='cfb_polymarket'"):
        t = POLY_TITLE.match(title or "")
        if t:
            titles[ev][frozenset((norm(t.group("a")), norm(t.group("b"))))] += 1
    for (ev,) in probe.execute("SELECT DISTINCT event_id FROM markets WHERE venue='cfb_polymarket'"):
        m = POLY_GAME.match(ev or "")
        if not m:
            out[("cfb_polymarket", ev)] = (None, "not a game (season or futures event)")
            continue
        if not titles.get(ev):
            out[("cfb_polymarket", ev)] = (None, "no 'A vs. B' title names the teams")
            continue
        names = set(titles[ev].most_common(1)[0][0])
        day = datetime.strptime(m.group(1), "%Y-%m-%d").date()
        g = _find(by_day, day, names)
        out[("cfb_polymarket", ev)] = _result(g, names)
    return out


def _result(g, names):
    if g is None:
        return (None, f"no game on date +/-1 for {sorted(names)}")
    if isinstance(g, list):
        return (None, f"{len(g)} candidate games for {sorted(names)}")
    return (g["game_id"], "matched")


def book_state(bid, ask):
    """Polymarket quotes an empty book as 0/1, whose mid of 0.5 means nothing."""
    if bid is None and ask is None:
        return "empty"
    if bid is not None and ask is not None and bid <= 0 and ask >= 1:
        return "empty_0_1"
    if bid is None or bid <= 0:
        return "ask_only"
    if ask is None or ask >= 1:
        return "bid_only"
    return "two_sided"


def load_games(cfb, season=2026):
    games = []
    for gid, start, home, away in cfb.execute(
            "SELECT game_id, start_ts, home_team, away_team FROM cfb_games "
            "WHERE valid_to_ts IS NULL AND start_ts IS NOT NULL AND season=?", (season,)):
        games.append({"game_id": gid, "start_ts": start, "nh": norm(home), "na": norm(away)})
    return games


def extract(probe, cfb):
    """Returns (market_rows, close_rows, measurements). Read-only on both."""
    lo, hi = probe.execute("SELECT MIN(ts), MAX(ts) FROM quotes").fetchone()
    games = load_games(cfb)
    kick = {g["game_id"]: g["start_ts"] for g in games}
    matched = match_games(probe, games)

    market_rows, close_rows = [], []
    notes = Counter()
    states = Counter()
    ages = []
    at_kick = in_window_games = 0
    after_window = no_quote = 0
    games_closed = defaultdict(set)
    for (venue, mid, ev, mtype, subj, line, title, open_ts, close_ts, settle_ts, result,
         first_seen, last_seen) in probe.execute(
            "SELECT venue, market_id, event_id, market_type, subject, line, title, open_ts, "
            "close_ts, settle_ts, result, first_seen, last_seen FROM markets "
            "ORDER BY venue, market_id"):
        gid, note = matched.get((venue, ev), (None, "event not seen"))
        notes[(venue, note)] += 1
        market_rows.append((venue, mid, ev, mtype, subj, line, title, open_ts, close_ts,
                            settle_ts, result, first_seen, last_seen, gid, note, CAPTURE))
        if gid is None:
            continue
        k = kick[gid]
        if not (lo <= k <= hi):
            after_window += 1
            continue
        q = probe.execute(
            "SELECT ts, best_bid, best_ask, mid, last, volume, open_interest FROM quotes "
            "WHERE venue=? AND market_id=? AND ts<? ORDER BY ts DESC LIMIT 1",
            (venue, mid, k)).fetchone()
        at_kick += probe.execute(
            "SELECT COUNT(*) FROM quotes WHERE venue=? AND market_id=? AND ts=?",
            (venue, mid, k)).fetchone()[0]
        if q is None:
            no_quote += 1
            continue
        qts, bid, ask, mid_p, last, vol, oi = q
        st = book_state(bid, ask)
        states[(venue, st)] += 1
        ages.append(k - qts)
        games_closed[venue].add(gid)
        close_rows.append((venue, mid, gid, k, qts, round(k - qts, 3), bid, ask, mid_p, last,
                           vol, oi, st, CAPTURE))

    agree = cfb.execute(
        "SELECT COUNT(*), SUM(g.start_ts = f.start_ts AND g.home_team = f.home_team "
        "AND g.away_team = f.away_team) FROM cfb_cfbd_games f JOIN cfb_games g "
        "ON g.game_id = f.game_id AND g.valid_to_ts IS NULL WHERE f.valid_to_ts IS NULL "
        "AND f.start_ts BETWEEN ? AND ?", (lo, hi)).fetchone()
    polls = defaultdict(list)
    for ts, venue, endpoint in probe.execute(
            "SELECT ts, venue, endpoint FROM poll_log ORDER BY ts"):
        polls[(venue, endpoint)].append(ts)

    m = [("probe.kickoff_agreement_with_cfbd", agree[1] or 0,
          f"of {agree[0]} CFBD games in the window agree on kickoff second and both names"),
         ("probe.markets", len(market_rows), None),
         ("probe.closes", len(close_rows), "last quote strictly before kickoff"),
         ("probe.closes_quote_at_kickoff_second", at_kick,
          "quotes stamped exactly at kickoff: where 'strictly before' and C01's 'at or before' differ"),
         ("probe.matched_markets_kicking_off_after_window", after_window, None),
         ("probe.matched_markets_without_pre_kickoff_quote", no_quote, None),
         ("probe.close_age_median_s", median(ages) if ages else None, None),
         ("probe.close_age_max_s", max(ages) if ages else None, None)]
    for venue in ("cfb_kalshi", "cfb_polymarket"):
        m.append((f"probe.games_with_a_close.{venue}", len(games_closed[venue]), None))
    for (venue, note), n in sorted(notes.items()):
        m.append((f"probe.match.{venue}.{note[:60]}", n, note))
    for (venue, st), n in sorted(states.items()):
        m.append((f"probe.close_book_state.{venue}.{st}", n, None))
    for (venue, endpoint), ts in sorted(polls.items()):
        gaps = [b - a for a, b in zip(ts, ts[1:])]
        if gaps:
            gaps.sort()
            m.append((f"probe.poll_gap_median_s.{venue}.{endpoint}", median(gaps),
                      f"p90 {gaps[int(0.9 * (len(gaps) - 1))]:.1f}s over {len(ts)} polls"))
    # The second process started 2026-09-11 16:00Z (CLAUDE.md, the double-write
    # incident). Poll counts per hour either side of it are the evidence.
    split = datetime(2026, 9, 11, 16, 0, tzinfo=ZoneInfo("UTC")).timestamp()
    for (venue, endpoint), ts in sorted(polls.items()):
        if endpoint != "quotes:game" or not ts:
            continue
        before = [t for t in ts if t < split]
        after = [t for t in ts if t >= split]
        for label, sel, a, b in (("before", before, ts[0], split), ("after", after, split, ts[-1])):
            hours = max((b - a) / 3600.0, 1e-9)
            m.append((f"probe.game_polls_per_hour.{venue}.{label}_double_write",
                      round(len(sel) / hours, 1), f"{len(sel)} polls over {hours:.1f}h"))
    sport_rows = probe.execute("SELECT sport, COUNT(*) FROM markets GROUP BY sport").fetchall()
    m.append(("probe.markets_labelled_sport", sum(n for s, n in sport_rows if s != "cfb"),
              f"probe's own sport column: {dict(sport_rows)}"))
    return market_rows, close_rows, m
