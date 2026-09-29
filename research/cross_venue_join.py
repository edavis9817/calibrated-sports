"""What the stored cross-venue join covers (a-53). Read-only.

    python -m research.cross_venue_join                  # 2026, every week
    python -m research.cross_venue_join --season 2026

The join lives in the store now: `jobs.map_markets --venue oddsapi` writes one
`markets` row per (book, prop, line) derived from the live quote log and links
it to the outcome a Kalshi rung already maps to. This script reads ONLY that -
no join is rebuilt here - and answers two questions:

1. Coverage. A TRIPLE is (week, player, stat, threshold). It carries a book
   price if any book line at that threshold is linked OR recorded book-only
   (both sides of one line price one claim, so over and under collapse), and a
   Kalshi rung if a Kalshi market maps to its outcome. Counted three ways:
   both, book only, Kalshi only.

2. The close. Every derived row carries `close_ts` (the Odds API kickoff) and
   `last_seen` (the book's last quote for that line), so how long before
   kickoff each game's last book snapshot landed is a field, not an exclusion.
   It is printed against both kickoff clocks, because they disagree by minutes
   and a "strictly before kickoff" close rule flips on which one it uses.

"Carries a Kalshi rung" is a MAPPING fact: the market existed and resolved. It
does not say the rung was two-sided or quoted at any instant.
"""
import argparse
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from venues import oddsapi  # noqa: E402

BENCH = tuple(f"oddsapi:{b}" for b in config.BOARD_BENCH_BOOKS)


def ro():
    return sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)


def parse_key(key):
    """(season, week, entity, stat, line, side) from an outcome key."""
    sport, season, wk, _mt, entity, stat, line, side = key.split("|")
    week = int(wk[2:]) if wk.startswith("wk") else None
    return int(season), week, entity, stat, float(line), side


def triples(con, season):
    """{venue_class: {(week, gsis, stat, line)}} for 'kalshi' and 'book'."""
    out = {"kalshi": set(), "book": set()}
    book_only = linked = 0
    for venue, week, gsis, stat, line in con.execute(
            "SELECT mo.venue, o.week, o.entity_id, o.stat, o.line FROM market_outcome mo "
            "JOIN outcomes o USING (outcome_id) WHERE o.season = ? "
            "AND o.entity_type = 'player' AND (mo.venue = 'kalshi' OR mo.venue LIKE 'oddsapi:%')",
            (season,)):
        if venue == "kalshi":
            out["kalshi"].add((week, gsis, stat, line))
        else:
            out["book"].add((week, gsis, stat, line))
            linked += 1
    for (reason,) in con.execute(
            "SELECT unmapped_reason FROM market_outcome WHERE venue LIKE 'oddsapi:%' "
            "AND outcome_id IS NULL AND unmapped_reason LIKE ?",
            (oddsapi.BookOnly.PREFIX + " %",)):
        got = oddsapi.parse_book_only(reason)
        if got is None:
            continue
        s, week, gsis, stat, line, _side = parse_key(got[1])
        if s == season:
            out["book"].add((week, gsis, stat, line))
            book_only += 1
    return out, {"book rows linked": linked, "book rows book-only": book_only}


def coverage(t):
    k, b = t["kalshi"], t["book"]
    cells = defaultdict(Counter)
    for x in k | b:
        week, _g, stat, _l = x
        cls = "both" if x in k and x in b else "kalshi only" if x in k else "book only"
        cells[(stat, week)][cls] += 1
    return cells


def closes(con, books=None):
    """Per game with derived benchmark-book prop rows, the book's last prop
    quote against BOTH kickoff clocks.

    `close_ts` on a derived row is the Odds API's commence time (from its
    discovery row); the nflverse kickoff comes through the linked outcomes'
    `event_id`, which is the game_id. The two disagree by minutes, and a close
    rule of "last snapshot strictly before kickoff, within N min" gives a
    different answer on each - so both leads are printed and neither is chosen
    here. `last_seen` is the last quote for a line; a negative lead means the
    book's final snapshot landed AFTER that clock's kickoff.
    """
    books = BENCH if books is None else books
    ph = ",".join("?" * len(books))
    game_of = dict(con.execute(
        f"SELECT m.event_id, MAX(o.event_id) FROM markets m "
        f"JOIN market_outcome mo ON mo.venue = m.venue AND mo.market_id = m.market_id "
        f"JOIN outcomes o ON o.outcome_id = mo.outcome_id "
        f"WHERE m.venue IN ({ph}) AND m.market_type = 'prop' GROUP BY m.event_id", books))
    kick = dict(con.execute("SELECT game_id, MAX(kickoff_ts) FROM nfl_games "
                            "WHERE kickoff_ts IS NOT NULL GROUP BY game_id"))
    out = []
    for eid, title, odds_kick, last, n in con.execute(
            f"SELECT event_id, MAX(title), MAX(close_ts), MAX(last_seen), COUNT(*) FROM markets "
            f"WHERE venue IN ({ph}) AND market_type = 'prop' "
            f"GROUP BY event_id ORDER BY MAX(close_ts)", books):
        gid = game_of.get(eid)
        nfl_kick = kick.get(gid)
        out.append({
            "event_id": eid, "game_id": gid, "title": title, "last": last, "rows": n,
            "lead_odds_min": (odds_kick - last) / 60.0 if odds_kick else None,
            "lead_nfl_min": (nfl_kick - last) / 60.0 if nfl_kick else None,
        })
    return out


def bench_price(con, outcome_id, at_ts, books=None):
    """(median de-vigged P(over), n books) for an OVER outcome at `at_ts`, read
    through the stored join, or (None, 0).

    The de-vig is `core.board.market_prob` itself - multiplicative, two-way
    quotes only, median over BOARD_BENCH_BOOKS - called, not copied. Each book
    contributes its latest over quote at or before `at_ts` and the under from
    the SAME snapshot. This is the latest-quote rule, not the Board's
    pulled-player rule (`board_read.ladder_at`), which also drops a book whose
    newest event snapshot no longer lists the player.
    """
    from core import board as B
    books = config.BOARD_BENCH_BOOKS if books is None else books
    venues = tuple(f"oddsapi:{b}" for b in books)
    ph = ",".join("?" * len(venues))
    ladder = defaultdict(dict)
    line = None
    for venue, mid in con.execute(
            f"SELECT venue, market_id FROM market_outcome WHERE outcome_id = ? "
            f"AND venue IN ({ph})", (outcome_id, *venues)):
        qmid, ln = oddsapi.split_prop_market_id(mid)
        head, side = qmid.rsplit("|", 1)
        if side.lower() != "over":
            continue
        over = con.execute(
            "SELECT ts, last FROM quotes WHERE venue = ? AND market_id = ? AND line = ? "
            "AND ts <= ? ORDER BY ts DESC LIMIT 1", (venue, qmid, ln, at_ts)).fetchone()
        if not over:
            continue
        under = con.execute(
            "SELECT last FROM quotes WHERE venue = ? AND market_id = ? AND line = ? AND ts = ?",
            (venue, f"{head}|Under", ln, over[0])).fetchone()
        line = ln
        ladder[ln][venue[8:]] = {"over": over[1], "under": under[0] if under else None}
    if line is None:
        return None, 0
    return B.market_prob(dict(ladder), line, books)


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%MZ")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", type=int, default=2026)
    a = ap.parse_args()
    con = ro()
    t, census = triples(con, a.season)
    if not t["book"] and not t["kalshi"]:
        raise SystemExit(f"no {a.season} triples at all in {config.DB_PATH} - "
                         "has jobs.map_markets --venue oddsapi run?")
    cells = coverage(t)
    print(f"season {a.season}  store {config.DB_PATH}")
    for k, v in census.items():
        print(f"  {k}: {v:,}")
    print(f"\n{'stat':<18}{'wk':>4}{'both':>8}{'book only':>11}{'kalshi only':>13}")
    tot = defaultdict(Counter)
    for (stat, week), c in sorted(cells.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0)):
        print(f"{stat:<18}{week:>4}{c['both']:>8,}{c['book only']:>11,}{c['kalshi only']:>13,}")
        tot[stat].update(c)
    print()
    grand = Counter()
    for stat, c in sorted(tot.items()):
        print(f"{stat:<18}{'all':>4}{c['both']:>8,}{c['book only']:>11,}{c['kalshi only']:>13,}")
        grand.update(c)
    print(f"{'TOTAL':<18}{'':>4}{grand['both']:>8,}{grand['book only']:>11,}"
          f"{grand['kalshi only']:>13,}")

    g = closes(con)
    print(f"\nbenchmark-book ({', '.join(b[8:] for b in BENCH)}) LAST prop quote vs kickoff, "
          f"minutes (negative = after that clock's kickoff); {len(g)} games")
    print(f"  {'game':<22}{'odds api':>10}{'nflverse':>10}")

    def fmt(v):
        return f"{'-':>10}" if v is None else f"{v:10.1f}"

    after = 0
    for x in g:
        lo, ln = x["lead_odds_min"], x["lead_nfl_min"]
        straddle = lo is not None and ln is not None and lo >= 0 > ln
        after += straddle
        note = "   <- before the Odds API kickoff, after nflverse's" if straddle else ""
        print(f"  {str(x['game_id'] or x['title'][:21]):<22}{fmt(lo)}{fmt(ln)}{note}")
    print(f"  games whose last book snapshot falls between the two kickoff clocks: {after}")

if __name__ == "__main__":
    main()
