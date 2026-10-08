"""The staleness gate (unit f-24): stale data, empty data, and data nobody reads.

    python -m analytics.staleness                      # all three checks, exit 1 on red
    python -m analytics.staleness --report out.md --json out.json
    python -m analytics.staleness --offline --quick    # no network, no depth scan

WHY THIS EXISTS. The contract gate checks that a published file matches a
schema. Nothing checked that a table reaches the current week, or that a
column we publish has a source behind it, so each of these was found by
accident: `f_team_game_pace` sat at 2026 week 1 for 18 days and nulled the
total on every matchup; `weather_at_kickoff` held 0 NFL rows from the day its
code landed; raw L2 books read 0 from 2026-09-15; `depth_charts` was archived
daily and parsed by nothing.

THREE CHECKS, AND WHAT EACH ONE CAN AND CANNOT SEE.

 1. FRESHNESS. Every table carrying `season` and `week` is DISCOVERED from the
    store, not listed here, so a table added next month is covered without
    anyone remembering it. Its newest week is compared with the last COMPLETED
    NFL week, read from the schedule's own kickoffs. Derived tables are held to
    a second standard that needs no tolerance at all: a build whose recorded
    pull date is older than the newest archived version of its source is
    stale, because its input moved and it did not.
 2. EMPTINESS. A table with no rows; a sport with no rows in a table other
    sports fill; a column that carried values over the same weeks last season
    and carries none this season; and a series that stopped - reported with
    the LAST NON-ZERO DATE, because "0" alone cannot say whether it has been 0
    for an hour or three weeks.
    And the MAPPED RATE (a-68): the share of a week's priority prop markets
    that carried an outcome BEFORE THEIR OWN KICKOFF (a-73: before kickoff,
    not at any time - by the Tuesday after, every market is mapped and the
    week reads healthy). Mapping ran three mornings a week while the venue
    listed on Thursday afternoon, so 87 of 1,589 week-4 props had an outcome
    before their own kickoff and nothing failed - a market nobody has looked
    at has no reason row, so the reason census read clean throughout. The
    denominator is therefore taken from `markets`, the TICKER'S OWN DATE and
    the schedule, never from anything the mapper wrote; the week being priced
    and the week just played are both read; and UNEXAMINED markets (no
    `market_outcome` row at all) are counted per venue as their own item.
 3. COLLECTED AND UNREAD. Tables and archived datasets crossed against the
    committed code by AST: what is written and never read, what is read only
    off every publish path, and (one free GitHub call) what the upstream
    releases offer that the dataset registry never fetches.

THERE ARE NO PER-TABLE TOLERANCES, ON PURPOSE. One clock, one standard. A gate
tuned until it is green measures nothing. The only way an item stops failing
is a row in `analytics/staleness_rulings.json`, which names who ruled, when
and why, is printed on every run, and ships EMPTY.

A RED ITEM IS RED; AN UNRULED ITEM IS A QUESTION. `UNRULED` is for a series
that stopped where the code cannot know whether stopping was the intent (a
one-off backfill and a dead capture look identical). They are listed with
their last date and fail only under `--strict`.

A CHECK THAT COULD NOT RUN IS NOT A PASS. Every query carries a time budget;
a query that is aborted, a store that is absent and a network call that fails
are `ERROR` items and the exit code is non-zero. `--offline` and `--quick`
skip a check out loud (`SKIPPED`), which is the only state that neither
passes nor fails.

READ ONLY. Every store is opened `mode=ro`, one short connection per query
group: a long read transaction pins the logger's WAL (CLAUDE.md, a-07). The
job writes nothing unless `--report` or `--json` names a path.
"""
import argparse
import ast
import datetime as dt
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass, field

RED, UNRULED, ERROR, SKIPPED, OK, RULED = "RED", "UNRULED", "ERROR", "SKIPPED", "ok", "ruled"
FAILING = (RED, ERROR)

# The stores this gate reads. The week clock is the NFL's, so freshness runs on
# the first three; the emptiness and unread checks are sport-agnostic and run
# on all of them.
NFL_STORES = ("market_log.db", "feeds.db", "analytics.db")
OTHER_STORES = ("cfb.db", "mlb.db")

# "Completed" needs ONE number and this is it: a week is complete once its last
# game kicked off this many hours ago. 12 is the figure the CFB weekly job
# already uses for the same question (CLAUDE.md, c-15); it is a definition of
# the clock, applied to every table alike, and not a tolerance on any of them.
GRACE_HOURS = 12

QUERY_BUDGET_S = 30
DEPTH_HORIZON_DAYS = 35
RULINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "staleness_rulings.json")
RELEASES_URL = "https://api.github.com/repos/nflverse/nflverse-data/releases?per_page=100"

# A schedule row is not a result: `nfl_games` holds every week of the season
# from the day the schedule is published, so its freshness is the newest week
# with a SCORE. This is a fact about what the table is, not a tolerance.
FRESHNESS_WHERE = {("market_log.db", "nfl_games"): "home_score IS NOT NULL"}

# Derived tables and the archived dataset each is built from. Each SQL returns
# (dataset, season-or-NULL, pull_date) per build row; the comparison is with
# `nflverse_versions`, whose data_version moves only when upstream content does.
BUILDS = (
    ("analytics.db", "f_spine_build", "SELECT 'pbp', season, pull_date FROM f_spine_build"),
    ("analytics.db", "f_ngs_build", "SELECT 'ngs_' || family, NULL, pull_date FROM f_ngs_build"),
    ("analytics.db", "f_onfield_build",
     "SELECT 'participation', season, pull_date FROM f_onfield_build"),
    ("analytics.db", "f_pbp_files", "SELECT dataset, season, pull_date FROM f_pbp_files"),
)

# Series that are meant to keep filling, each with where that is written down.
# A stopped series NOT named here is UNRULED rather than RED.
WATCHED_RAW = {
    "kalshi_depth": "raw L2 books for priority props (a-65: 0 since 2026-09-15)",
    "polymarket_depth": "raw Polymarket books (a-65: 0 since 2026-09-15)",
    "kalshi": "the logger's own venue archive",
    "polymarket": "the logger's own venue archive",
    "oddsapi": "the logger's own venue archive",
    "nflverse": "the weekly nflverse mirror",
}
WATCHED_FEEDS = {
    "weather": "weather_at_kickoff, which the matchup file publishes (a-64)",
    "injuries": "injury_reports, the Wed-Fri report",
}
# A sport absent from a table another sport fills is RED only where something
# published is known to need it; elsewhere the code cannot know the intent.
WATCHED_PARTITIONS = {
    ("feeds.db", "weather_at_kickoff", "nfl"): "the matchup file publishes wind and temperature (a-64)",
}
DEPTH_VENUES = ("kalshi", "polymarket")
# `source_health.detail` counters that must not read zero, per source.
HEALTH_COUNTERS = {"depth_capture": ("kalshi_rows", "poly_rows", "raw_books_kept")}

# Where "published" starts. A module is on a publish path when it is reachable
# from one of these by imports or by a `-m package.module` string.
ENTRY_MODULES = ("jobs.weekly_refresh", "jobs.publish_preflight")
ENTRY_SCRIPTS = (".cmd", ".ps1")


@dataclass
class Item:
    check: str
    key: str
    status: str
    statement: str
    detail: dict = field(default_factory=dict)


class GateReport:
    """What the gate found. Refuses truth-testing: read `.clean` or `.statement`."""

    def __init__(self, items, clock, strict=False, rulings=()):
        self.items, self.clock, self.strict, self.rulings = list(items), clock, strict, list(rulings)

    def count(self, status):
        return sum(1 for i in self.items if i.status == status)

    @property
    def failing(self):
        bad = FAILING + ((UNRULED,) if self.strict else ())
        return [i for i in self.items if i.status in bad]

    @property
    def clean(self):
        return not self.failing

    @property
    def statement(self):
        return ("staleness gate: %d red, %d error, %d unruled, %d skipped, %d ruled, %d ok - %s"
                % (self.count(RED), self.count(ERROR), self.count(UNRULED), self.count(SKIPPED),
                   self.count(RULED), self.count(OK), "CLEAN" if self.clean else "FAILING"))

    def __bool__(self):
        raise TypeError("GateReport is not a boolean: read .clean (the verdict) "
                        "or .statement (what was checked)")


# --------------------------------------------------------------------------
# reading

def _uri(path):
    return "file:" + os.path.abspath(path).replace("\\", "/") + "?mode=ro"


def query(path, sql, args=(), budget=None):
    """One read-only query on one short connection, aborted past `budget`.

    Raises on abort: an interrupted query has no answer, and returning [] would
    make "ran out of time" indistinguishable from "the table is empty".
    """
    budget = QUERY_BUDGET_S if budget is None else budget
    conn = sqlite3.connect(_uri(path), uri=True, timeout=5)
    started = time.time()
    conn.set_progress_handler(lambda: 1 if time.time() - started > budget else 0, 50000)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def tables(path):
    """{table: [column, ...]} for one store."""
    names = [r[0] for r in query(path, "SELECT name FROM sqlite_master WHERE type='table' "
                                       "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    return {n: [r[1] for r in query(path, 'PRAGMA table_info("%s")' % n)] for n in names}


def day(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d")


def current_season(now):
    """Same boundary as `nflverse.current_season`: March turns the league year."""
    d = dt.datetime.fromtimestamp(now, dt.timezone.utc)
    return d.year if d.month >= 3 else d.year - 1


# --------------------------------------------------------------------------
# the clock

@dataclass
class Clock:
    season: int
    week: int            # last COMPLETED week
    ended_ts: float      # that week's last kickoff
    in_progress: object  # a week with a kickoff behind us and not yet complete, or None
    statement: str

    @property
    def pos(self):
        return (self.season, self.week)


def week_clock(market_log, now, grace_hours=None):
    """The last completed NFL week, from the schedule's own kickoffs.

    The schedule is published for the whole season in advance, so this does not
    depend on anything having been ingested recently - only on the season's
    schedule existing. When it does not, this RAISES: every week comparison
    below would otherwise be made against nothing and pass.
    """
    grace_hours = GRACE_HOURS if grace_hours is None else grace_hours
    season = current_season(now)
    for s in (season, season - 1):
        rows = query(market_log, "SELECT week, MAX(kickoff_ts) FROM nfl_games "
                                 "WHERE sport='nfl' AND season=? AND kickoff_ts IS NOT NULL "
                                 "GROUP BY week ORDER BY week", (s,))
        if s == season and not rows:
            raise LookupError("no nfl_games schedule rows for season %d: the week clock "
                              "cannot be established" % season)
        done = [(w, k) for w, k in rows if k + grace_hours * 3600 <= now]
        started = [w for w, k in rows if k <= now and k + grace_hours * 3600 > now]
        if done:
            w, k = done[-1]
            return Clock(s, w, k, started[0] if started and s == season else None,
                         "last completed week is %d week %d (final kickoff %s UTC + %dh grace)"
                         % (s, w, dt.datetime.fromtimestamp(k, dt.timezone.utc)
                            .strftime("%Y-%m-%d %H:%M"), grace_hours))
    raise LookupError("no completed week in %d or %d" % (season, season - 1))


def behind(clock, pos):
    """How far `pos` (season, week) trails the clock, as (weeks or None, words)."""
    if pos is None:
        return None, "no rows at all"
    if pos >= clock.pos:
        return 0, "current"
    if pos[0] == clock.season:
        n = clock.week - pos[1]
        return n, "%d week%s behind" % (n, "" if n == 1 else "s")
    return None, "%d season%s behind" % (clock.season - pos[0], "" if clock.season - pos[0] == 1 else "s")


# --------------------------------------------------------------------------
# 1. freshness

def week_tables(path):
    return {t: c for t, c in tables(path).items() if "season" in c and "week" in c}


def check_freshness(stores, clock):
    out = []
    for name, path in stores.items():
        if name not in NFL_STORES:
            continue
        for table, cols in sorted(week_tables(path).items()):
            key = "%s:%s" % (name, table)
            where = ["season IS NOT NULL", "week IS NOT NULL"]
            if "sport" in cols:
                where.append("sport='nfl'")
            if (name, table) in FRESHNESS_WHERE:
                where.append(FRESHNESS_WHERE[(name, table)])
            w = " AND ".join(where)
            try:
                top = query(path, 'SELECT season, MAX(week) FROM "%s" WHERE %s GROUP BY season '
                                  "ORDER BY season DESC LIMIT 1" % (table, w))
                weeks = [r[0] for r in query(
                    path, 'SELECT DISTINCT week FROM "%s" WHERE %s AND season=?' % (table, w),
                    (clock.season,))]
            except sqlite3.OperationalError as e:
                out.append(Item("freshness", key, ERROR, "%s: query failed (%s)" % (key, e)))
                continue
            pos = tuple(top[0]) if top else None
            n, words = behind(clock, pos)
            # A table that reaches the clock can still have a hole behind it.
            missing = (sorted(set(range(1, min(clock.week, pos[1]) + 1)) - set(weeks))
                       if pos and pos[0] == clock.season else [])
            detail = {"newest": pos, "expected": clock.pos, "weeks_behind": n,
                      "holes": missing, "where": w}
            if words != "current":
                out.append(Item("freshness", key, RED,
                                "%s newest is %s; expected %d week %d (%s)"
                                % (key, "season %d week %d" % pos if pos else "nothing",
                                   clock.season, clock.week, words), detail))
            elif missing:
                out.append(Item("freshness", key, RED,
                                "%s reaches week %d but has no rows for week(s) %s of %d"
                                % (key, pos[1], missing, clock.season), detail))
            else:
                out.append(Item("freshness", key, OK,
                                "%s reaches %d week %d" % (key, pos[0], pos[1]), detail))
    return out


def check_builds(stores, clock):
    """A build is stale when its source moved after the pull it was built from."""
    out = []
    ml = stores.get("market_log.db")
    try:
        versions = {(d, s): v for d, s, v in query(
            ml, "SELECT dataset, season, MAX(data_version) FROM nflverse_versions GROUP BY 1, 2")}
    except (sqlite3.OperationalError, TypeError) as e:
        return [Item("freshness", "builds", ERROR, "nflverse_versions unreadable (%s)" % e)]
    for name, table, sql in BUILDS:
        key = "%s:%s" % (name, table)
        if name not in stores:
            out.append(Item("freshness", key, ERROR, "%s: store not found" % key))
            continue
        try:
            rows = query(stores[name], sql)
        except sqlite3.OperationalError as e:
            out.append(Item("freshness", key, ERROR, "%s: query failed (%s)" % (key, e)))
            continue
        built = {(d, s): p for d, s, p in rows}
        stale = [(d, s, p, versions[(d, s)]) for (d, s), p in sorted(built.items(), key=str)
                 if (d, s) in versions and p < versions[(d, s)]]
        datasets = {d for d, _ in built}
        never = sorted(((d, s) for (d, s) in versions if d in datasets and (d, s) not in built
                        and s is not None and s >= clock.season - 1), key=str)
        if stale or never:
            worst = max(stale, key=lambda r: r[3]) if stale else None
            words = []
            if stale:
                words.append("%d build row(s) older than their source; e.g. %s%s built from "
                             "pull %s, source last moved %s"
                             % (len(stale), worst[0], "" if worst[1] is None else " %d" % worst[1],
                                worst[2], worst[3]))
            if never:
                words.append("never built: %s" % never)
            out.append(Item("freshness", key, RED, "%s: %s" % (key, "; ".join(words)),
                            {"stale": stale, "never_built": never}))
        else:
            out.append(Item("freshness", key, OK, "%s: every build row is at its source's newest "
                                                   "version (%d rows)" % (key, len(built))))
    return out


def check_kickoff_tables(stores, clock):
    """Tables keyed by game rather than by week: the newest NFL kickoff they hold.

    Same standard as the week tables in time form - the table must hold a game
    from the last completed week or later. `weather_at_kickoff` is the case
    this exists for: 2,171 rows of recorded history satisfy "has NFL rows" and
    say nothing about a game anyone is about to play.
    """
    out = []
    for name, path in stores.items():
        if name not in NFL_STORES:
            continue
        for table, cols in sorted(tables(path).items()):
            if "kickoff_ts" not in cols or ("season" in cols and "week" in cols):
                continue
            key = "%s:%s" % (name, table)
            sport = " AND sport='nfl'" if "sport" in cols else ""
            try:
                top = query(path, 'SELECT MAX(kickoff_ts), COUNT(*) FROM "%s" WHERE kickoff_ts '
                                  "IS NOT NULL%s" % (table, sport))[0]
            except sqlite3.OperationalError as e:
                out.append(Item("freshness", key, ERROR, "%s: query failed (%s)" % (key, e)))
                continue
            if top[0] is None:
                out.append(Item("freshness", key, RED, "%s holds no NFL game at all" % key))
            elif top[0] < clock.ended_ts:
                out.append(Item("freshness", key, RED,
                                "%s newest NFL kickoff is %s; it holds no game from %d week %d or "
                                "later (%d rows)" % (key, day(top[0]), clock.season, clock.week, top[1]),
                                {"newest_kickoff": day(top[0])}))
            else:
                out.append(Item("freshness", key, OK, "%s reaches kickoff %s" % (key, day(top[0]))))
    return out


def check_metrics(stores, clock):
    """A 'current' metric computed before the last completed week ended cannot contain it."""
    path = stores.get("analytics.db")
    try:
        rows = query(path, "SELECT metric, computed_ts FROM f_metrics WHERE availability='current'")
    except (sqlite3.OperationalError, TypeError) as e:
        return [Item("freshness", "analytics.db:f_metrics", ERROR, "f_metrics unreadable (%s)" % e)]
    old = sorted((m, c) for m, c in rows if c is None or c < clock.ended_ts)
    if not rows:
        return [Item("freshness", "analytics.db:f_metrics", RED,
                     "f_metrics has no metric marked availability='current'")]
    if old:
        return [Item("freshness", "analytics.db:f_metrics", RED,
                     "%d of %d 'current' metrics were computed before %d week %d ended; "
                     "newest of them %s, oldest %s"
                     % (len(old), len(rows), clock.season, clock.week,
                        day(max(c for _, c in old if c)), day(min(c for _, c in old if c))),
                     {"metrics": [m for m, _ in old]})]
    return [Item("freshness", "analytics.db:f_metrics", OK,
                 "all %d 'current' metrics computed after the last completed week" % len(rows))]


def check_health(stores, now):
    path = stores.get("market_log.db")
    try:
        rows = query(path, "SELECT source, ok, detail, updated_ts FROM source_health")
    except (sqlite3.OperationalError, TypeError) as e:
        return [Item("freshness", "source_health", ERROR, "source_health unreadable (%s)" % e)]
    out = []
    for source, ok, detail, updated in sorted(rows):
        if not ok:
            out.append(Item("freshness", "source_health:%s" % source, RED,
                            "source_health '%s' is failing as of %s: %s"
                            % (source, day(updated) if updated else "?", (detail or "")[:120])))
    out.append(Item("freshness", "source_health", OK,
                    "%d source_health rows read, %d failing" % (len(rows), len(out)),
                    {"ages_days": {s: round((now - u) / 86400, 1) for s, _, _, u in rows if u}}))
    return out


# --------------------------------------------------------------------------
# 2. emptiness

INFORMATIVE = '("{c}" IS NOT NULL AND "{c}" != 0 AND "{c}" != \'\')'


def check_empty_tables(stores):
    out = []
    for name, path in stores.items():
        tabs = tables(path)
        empty = []
        for table in tabs:
            try:
                if not query(path, 'SELECT 1 FROM "%s" LIMIT 1' % table):
                    empty.append(table)
            except sqlite3.OperationalError as e:
                out.append(Item("emptiness", "%s:%s" % (name, table), ERROR, "query failed (%s)" % e))
        for table in empty:
            out.append(Item("emptiness", "%s:%s" % (name, table), RED,
                            "%s:%s exists and holds 0 rows" % (name, table)))
        out.append(Item("emptiness", "%s:tables" % name, OK,
                        "%s: %d tables, %d empty" % (name, len(tabs), len(empty))))
    return out


def check_sport_partitions(stores, budget=5):
    """A table that some sport fills and another sport in the same store does not."""
    out = []
    for name, path in stores.items():
        per = {}
        for table, cols in tables(path).items():
            if "sport" not in cols:
                continue
            try:
                per[table] = {r[0]: r[1] for r in query(
                    path, 'SELECT sport, COUNT(*) FROM "%s" GROUP BY sport' % table, budget=budget)}
            except sqlite3.OperationalError:
                out.append(Item("emptiness", "%s:%s:sport" % (name, table), SKIPPED,
                                "%s:%s too large to group by sport inside %ds; not checked"
                                % (name, table, budget)))
        sports = {s for d in per.values() for s in d if s}
        for table, d in sorted(per.items()):
            for s in sorted(sports - set(d)):
                if d:
                    watched = WATCHED_PARTITIONS.get((name, table, s))
                    out.append(Item("emptiness", "%s:%s:sport=%s" % (name, table, s),
                                    RED if watched else UNRULED,
                                    "%s:%s holds 0 rows for sport '%s' (%s)%s"
                                    % (name, table, s, ", ".join("%s %d" % kv for kv in sorted(d.items())),
                                       "" if watched else " - no ruling on whether it should hold any")))
    return out


def check_columns(stores, clock):
    """A column that filled over the same weeks last season and is empty this season.

    The control is the table's OWN prior season over weeks 1..W, so a column
    that is rare by nature shows "prior 2, now 0" and one that died shows
    "prior 5,000, now 0". Both are red; the pair of counts is what a reader
    rules on. No threshold separates them here, on purpose.
    """
    out = []
    for name, path in stores.items():
        if name not in NFL_STORES:
            continue
        for table, cols in sorted(week_tables(path).items()):
            key = "%s:%s" % (name, table)
            body = [c for c in cols if c not in ("season", "week")]
            sport = " AND sport='nfl'" if "sport" in cols else ""
            sel = ", ".join(
                "SUM(CASE WHEN season=:cur AND week<=:w AND %s THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN season=:cur-1 AND week<=:w AND %s THEN 1 ELSE 0 END), "
                "MAX(CASE WHEN %s THEN season*100+week END)"
                % ((INFORMATIVE.format(c=c),) * 3) for c in body)
            try:
                row = query(path, 'SELECT SUM(season=:cur AND week<=:w), SUM(season=:cur-1 AND week<=:w), '
                                  '%s FROM "%s" WHERE season IS NOT NULL%s' % (sel, table, sport),
                            {"cur": clock.season, "w": clock.week})[0]
            except sqlite3.OperationalError as e:
                out.append(Item("emptiness", key + ":columns", ERROR,
                                "%s: column scan failed (%s)" % (key, e)))
                continue
            n_cur, n_prev = row[0] or 0, row[1] or 0
            bad = 0
            for i, c in enumerate(body):
                cur, prev, last = row[2 + 3 * i] or 0, row[3 + 3 * i] or 0, row[4 + 3 * i]
                last_words = "never" if last is None else "%d week %d" % divmod(last, 100)
                if last is None:
                    bad += 1
                    out.append(Item("emptiness", "%s.%s" % (key, c), RED,
                                    "%s.%s has never held a value in any season" % (key, c),
                                    {"last_informative": None}))
                elif n_cur and prev and not cur:
                    bad += 1
                    out.append(Item("emptiness", "%s.%s" % (key, c), RED,
                                    "%s.%s is empty for %d weeks 1-%d (%d rows) and held %d values "
                                    "over the same weeks of %d; last value: %s"
                                    % (key, c, clock.season, clock.week, n_cur, prev,
                                       clock.season - 1, last_words),
                                    {"prior": prev, "now": 0, "last_informative": last_words}))
            out.append(Item("emptiness", key + ":columns", OK,
                            "%s: %d columns scanned, %d empty (%d rows this season to week %d, "
                            "%d last)" % (key, len(body), bad, n_cur, clock.week, n_prev)))
    return out


def _stopped(check, key, last_day, today, watched, what, extra=""):
    """One series' item: current, or stopped with its last non-zero date."""
    if last_day is None:
        return Item(check, key, RED if watched else UNRULED, "%s has never held a non-zero %s" % (key, what))
    gap = (dt.date.fromisoformat(today) - dt.date.fromisoformat(last_day)).days
    if gap <= 1:
        return Item(check, key, OK, "%s last non-zero %s (current)%s" % (key, last_day, extra))
    return Item(check, key, RED if watched else UNRULED,
                "%s last non-zero %s, %d days ago%s%s"
                % (key, last_day, gap, extra, "" if watched else " - no ruling on whether it should continue"),
                {"last_nonzero": last_day, "days": gap, "watched": watched or None})


def check_series(stores, now, quick=False, horizon=None):
    """Series that stopped, each with the last date it was non-zero."""
    out, today = [], day(now)
    horizon = DEPTH_HORIZON_DAYS if horizon is None else horizon
    ml, feeds = stores.get("market_log.db"), stores.get("feeds.db")
    if ml:
        for venue, last, n in query(ml, "SELECT venue, MAX(day), COUNT(*) FROM raw_shards GROUP BY venue"):
            out.append(_stopped("emptiness", "raw_shards:%s" % venue, last, today,
                                WATCHED_RAW.get(venue), "shard", " (%d shards)" % n))
        polls = query(ml, "SELECT venue, endpoint, MAX(ts), MAX(CASE WHEN COALESCE(n_quotes,0)>0 "
                          "OR (endpoint='discovery' AND COALESCE(n_markets,0)>0) THEN ts END) "
                          "FROM poll_log GROUP BY venue, endpoint")
        for venue, endpoint, last_poll, last_full in polls:
            key = "poll_log:%s:%s" % (venue, endpoint)
            polling = (now - last_poll) < 2 * 86400
            item = _stopped("emptiness", key, day(last_full) if last_full else None, today,
                            "still polled, returning nothing" if polling else None, "poll")
            if not polling and item.status != OK:
                item.statement += " (last polled at all %s)" % day(last_poll)
            out.append(item)
        health = {s: d for s, d in query(ml, "SELECT source, detail FROM source_health")}
        zero = {}
        for source, counters in HEALTH_COUNTERS.items():
            found = dict(re.findall(r"(\w+)=(\d+)", health.get(source) or ""))
            for c in counters:
                if c not in found:
                    out.append(Item("emptiness", "source_health:%s.%s" % (source, c), ERROR,
                                    "counter %s not found in source_health '%s' detail" % (c, source)))
                elif int(found[c]) == 0:
                    zero[c] = source
        if quick:
            out.append(Item("emptiness", "market_depth:by_venue", SKIPPED,
                            "market_depth by venue not scanned (--quick)"))
            last_depth = {}
        else:
            last_depth = depth_last_days(ml, now, horizon)
            for venue in DEPTH_VENUES:
                d = last_depth.get(venue)
                if d is None:
                    out.append(Item("emptiness", "market_depth:%s" % venue, RED,
                                    "market_depth has no '%s' row in the last %d days (scan horizon; "
                                    "the last non-zero date is older than %s)"
                                    % (venue, horizon, day(now - horizon * 86400))))
                else:
                    out.append(_stopped("emptiness", "market_depth:%s" % venue, d, today,
                                        "depth capture, both venues", "row"))
        shard_last = {v: l for v, l, _ in query(ml, "SELECT venue, MAX(day), COUNT(*) FROM raw_shards GROUP BY venue")}
        since = {"poly_rows": last_depth.get("polymarket"), "kalshi_rows": last_depth.get("kalshi"),
                 "raw_books_kept": max([d for d in (shard_last.get("kalshi_depth"),
                                                    shard_last.get("polymarket_depth")) if d] or [None],
                                       key=lambda x: x or "")}
        for c, source in sorted(zero.items()):
            out.append(Item("emptiness", "source_health:%s.%s" % (source, c), RED,
                            "%s.%s reads 0 now; last non-zero %s"
                            % (source, c, since.get(c) or ("not scanned" if quick else "beyond the scan horizon")),
                            {"last_nonzero": since.get(c)}))
    if feeds:
        for feed, last, n in query(feeds, "SELECT feed, MAX(fetched_ts), COUNT(*) FROM feeds_raw_files GROUP BY feed"):
            out.append(_stopped("emptiness", "feeds_raw_files:%s" % feed, day(last) if last else None,
                                today, WATCHED_FEEDS.get(feed), "fetch", " (%d files)" % n))
    return out


# --------------------------------------------------------------------------
# 2b. the mapped rate (a-68)

# The series the Board, the depth allowlist and the model all lean on: Kalshi's
# receptions and rush-attempt ladders (CLAUDE.md, priority markets).
PRIORITY_PROP_SERIES = ("KXNFLREC", "KXNFLRSHATT")
# ONE floor, stated. Caught up, the rate measured 1.000 in weeks 2 to 5 and
# 0.998 in week 1 (3 of 1,722: one nickname the crosswalk does not carry). In
# the failure it was 0.06 (week 2) and 0.05 (week 4) at kickoff. 0.95 sits far
# below every healthy reading and far above every failed one.
MAPPED_RATE_FLOOR = 0.95
# A market younger than this is not yet counted: the logger maps on a 10-minute
# timer, so a rung listed a minute ago is unmapped by design, not by defect.
MAPPING_GRACE_S = 1800
# No priority prop listed at all is "no information" until this close to the
# week's first kickoff, and RED after it: props list about 7h before the
# Thursday game (measured 17:00Z against a 00:15Z kickoff, weeks 3 and 4).
LISTED_BY_S = 4 * 3600
# The week just played stays in the gate this long after its last kickoff. The
# current-week reading disappears when the week rolls, and a Tuesday read of
# "everything is mapped" is exactly how weeks 2 and 4 passed for healthy
# (a-67 read the table after that morning's full pass). Eight days is one
# week of memory, so the offseason does not carry a January reading into July.
PREVIOUS_WEEK_WITHIN_S = 8 * 86400
_TICKER_DATE = re.compile(r"^(?P<series>[A-Z0-9]+)-(?P<yy>\d\d)(?P<mon>[A-Z]{3})(?P<dd>\d\d)"
                          r"(?P<blob>[A-Z]*)")
_MONTHS = {m: i + 1 for i, m in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"))}
# nflverse code -> the spellings Kalshi concatenates into a ticker. This
# module's own table for the reason `ticker_day` has its own pattern.
KALSHI_TEAM_FORMS = {"LA": ("LA", "LAR"), "JAX": ("JAX", "JAC"), "WAS": ("WAS", "WSH"),
                     "LV": ("LV", "LVR")}
UNEXAMINED = "(never looked at: no market_outcome row)"


def ticker_day(ticker):
    """(series, UTC-midnight ts of the game date in the ticker) or None.

    Read from the ticker by this module's own pattern on purpose: the week a
    market belongs to must not come from the mapper whose output is on trial.
    """
    m = _TICKER_DATE.match(ticker or "")
    if not m or m.group("mon") not in _MONTHS:
        return None
    try:
        d = dt.datetime(2000 + int(m.group("yy")), _MONTHS[m.group("mon")],
                        int(m.group("dd")), tzinfo=dt.timezone.utc)
    except ValueError:
        return None
    return m.group("series"), d.timestamp()


def ticker_game(ticker):
    """(gameday 'YYYY-MM-DD', team blob) from a ticker, or None. The date in a
    ticker is the game's LOCAL day, which is `nfl_games.gameday`."""
    m = _TICKER_DATE.match(ticker or "")
    if not m or m.group("mon") not in _MONTHS or not m.group("blob"):
        return None
    return ("20%s-%02d-%s" % (m.group("yy"), _MONTHS[m.group("mon")], m.group("dd")),
            m.group("blob"))


def game_schedule(market_log, season):
    """({(gameday, blob): (week, kickoff_ts)}, {week: (first, last)}).

    Every spelling of the two codes, away then home - the order Kalshi writes.
    This is how an UNMAPPED market gets a kickoff: through the schedule, never
    through the mapping it does not have.
    """
    sched, weeks = {}, {}
    for week, gameday, home, away, kick in query(
            market_log, "SELECT week, gameday, home_team, away_team, MAX(kickoff_ts) "
                        "FROM nfl_games WHERE sport='nfl' AND season=? "
                        "AND kickoff_ts IS NOT NULL GROUP BY game_id", (season,)):
        first, last = weeks.get(week, (kick, kick))
        weeks[week] = (min(first, kick), max(last, kick))
        for a in KALSHI_TEAM_FORMS.get(away, (away,)):
            for h in KALSHI_TEAM_FORMS.get(home, (home,)):
                sched[(gameday, "%s%s" % (a, h))] = (week, kick)
    return sched, weeks


def current_week(market_log, now, grace_hours=None):
    """(season, week, first_kickoff, last_kickoff) of the week being priced:
    the first week whose final kickoff plus the grace is still ahead."""
    grace_hours = GRACE_HOURS if grace_hours is None else grace_hours
    season = current_season(now)
    rows = query(market_log, "SELECT week, MIN(kickoff_ts), MAX(kickoff_ts) FROM nfl_games "
                             "WHERE sport='nfl' AND season=? AND kickoff_ts IS NOT NULL "
                             "GROUP BY week ORDER BY week", (season,))
    for w, first, last in rows:
        if last + grace_hours * 3600 > now:
            return season, w, first, last
    return None


def check_mapped_rate(stores, now, floor=None, grace_s=None):
    """Share of a week's priority prop markets that carried an outcome BEFORE
    THEIR OWN KICKOFF. Per series, RED below the floor.

    Before kickoff, not at any time (a-73). "Mapped today" is true of every
    market by the Tuesday after, which is how weeks 2 and 4 read as healthy
    with 6% of their props mapped in time: a market mapped after its game is
    worth nothing to depth capture, the Board or the kickoff tiers. So for a
    game that has kicked off, a market counts only if its outcome existed
    before the kickoff; for a game still ahead, being mapped now is being in
    time. Two weeks are read: the one being priced, and the one just played
    (for PREVIOUS_WEEK_WITHIN_S), because the first reading vanishes when the
    week rolls and the second is what a Tuesday reader needs.

    RELIES ON `outcomes.created_ts` AS THE TIME A MARKET WAS FIRST MAPPED.
    `market_outcome.mapped_ts` cannot say it: every full pass rewrites it.
    `created_ts` is written once and survives a re-upsert, and it is the
    market's own first-mapped time only where the market's venue is the one
    that creates the outcome - true of Kalshi player props, which is all this
    reads (Polymarket and the books link to player outcomes, never create).

    Reported apart, because they are different defects: `late` is mapped after
    kickoff (the cadence failure), `no_row` is the mapper not having looked,
    `refused` is the mapper saying no.
    """
    floor = MAPPED_RATE_FLOOR if floor is None else floor
    grace_s = MAPPING_GRACE_S if grace_s is None else grace_s
    path = stores.get("market_log.db")
    if path is None:
        return [Item("emptiness", "mapped_rate", ERROR, "market_log.db not found: mapped rate not read")]
    season = current_season(now)
    sched, weeks = game_schedule(path, season)
    cur = current_week(path, now)
    out = []
    targets = {}                                   # week -> is it the one being priced
    if cur is None:
        out.append(Item("emptiness", "mapped_rate", SKIPPED,
                        "no week ahead in the %d schedule: there is no current slate to map" % season))
    else:
        targets[cur[1]] = True
    played = [w for w, (_, last) in weeks.items()
              if last + GRACE_HOURS * 3600 <= now and now - last <= PREVIOUS_WEEK_WITHIN_S]
    if played:
        targets.setdefault(max(played), False)
    if not targets:
        return out
    like = " OR ".join("m.market_id LIKE ?" for _ in PRIORITY_PROP_SERIES)
    rows = query(path, "SELECT m.market_id, m.first_seen, mo.market_id IS NOT NULL, "
                       "mo.outcome_id IS NOT NULL, mo.unmapped_reason, o.created_ts "
                       "FROM markets m LEFT JOIN market_outcome mo "
                       "ON mo.venue = m.venue AND mo.market_id = m.market_id "
                       "LEFT JOIN outcomes o ON o.outcome_id = mo.outcome_id "
                       "WHERE m.venue = 'kalshi' AND (%s)" % like,
                 tuple(s + "-%" for s in PRIORITY_PROP_SERIES))
    per = {(w, s): {"n": 0, "in_time": 0, "late": 0, "no_row": 0, "refused": 0, "young": 0,
                    "listed_late": 0, "oldest": None, "reasons": {}}
           for w in targets for s in PRIORITY_PROP_SERIES}
    undated, unplaced = [], []
    for mid, seen, has_row, mapped, reason, created in rows:
        td = ticker_day(mid)
        if td is None or td[0] not in PRIORITY_PROP_SERIES:
            undated.append(mid)
            continue
        hit = sched.get(ticker_game(mid) or ())
        if hit is None:
            # a ticker's date is the game's local day; a night kickoff is the next day in UTC
            if any(weeks[w][0] - 36 * 3600 <= td[1] <= weeks[w][1] for w in targets):
                unplaced.append(mid)
            continue
        week, kick = hit
        if week not in targets:
            continue
        c = per[(week, td[0])]
        if kick <= now:
            # the game is under way or over: only an outcome that existed
            # before the kickoff was any use to it
            if seen is None or seen > kick - grace_s:
                c["listed_late"] += 1       # listed too late to have been mapped in time
                continue
            c["n"] += 1
            if mapped and created is not None and created < kick:
                c["in_time"] += 1
                continue
            key = ("late" if mapped and created is not None else
                   "no_row" if not has_row else "refused")
            why = ("(mapped only after kickoff)" if key == "late" else UNEXAMINED if key == "no_row"
                   else (reason or "(outcome_id with no outcomes row)")[:60])
        else:
            if not mapped and seen is not None and now - seen < grace_s:
                c["young"] += 1
                continue
            c["n"] += 1
            if mapped:
                c["in_time"] += 1
                continue
            key = "no_row" if not has_row else "refused"
            why = UNEXAMINED if key == "no_row" else (reason or "?")[:60]
        c[key] += 1
        if key != "late":
            c["oldest"] = seen if c["oldest"] is None or (seen or now) < c["oldest"] else c["oldest"]
        c["reasons"][why] = c["reasons"].get(why, 0) + 1
    if undated:
        out.append(Item("emptiness", "mapped_rate:undated", RED,
                        "%d priority prop ticker(s) carry no readable game date, so they are in "
                        "no week's rate (first: %s)" % (len(undated), undated[0]),
                        {"tickers": undated[:20]}))
    if unplaced:
        out.append(Item("emptiness", "mapped_rate:unplaced", RED,
                        "%d priority prop ticker(s) dated inside a week under test match no game in "
                        "the schedule, so they have no kickoff and are in no rate (first: %s)"
                        % (len(unplaced), unplaced[0]), {"tickers": unplaced[:20]}))
    for week in sorted(targets, reverse=True):
        pricing = targets[week]
        first = weeks[week][0]
        where = "%d week %d" % (season, week)
        for s in PRIORITY_PROP_SERIES:
            c = per[(week, s)]
            key = "mapped_rate:kalshi:%s" % s if pricing else "mapped_rate:kalshi:%s:%dw%02d" % (s, season, week)
            detail = dict(c, season=season, week=week, floor=floor, pricing=pricing)
            if c["n"] == 0:
                if now >= first - LISTED_BY_S:
                    out.append(Item("emptiness", key, RED,
                                    "%s has NO %s market listed for %s and its first kickoff is %s "
                                    "UTC: nothing to map is a discovery failure, not a pass"
                                    % ("kalshi", s, where, dt.datetime.fromtimestamp(
                                        first, dt.timezone.utc).strftime("%Y-%m-%d %H:%M")), detail))
                else:
                    out.append(Item("emptiness", key, SKIPPED,
                                    "no %s market listed yet for %s (%d under %d min old): no rate "
                                    "to read, which is not a pass" % (s, where, c["young"], grace_s // 60),
                                    detail))
                continue
            rate = c["in_time"] / c["n"]
            detail["rate"] = round(rate, 4)
            head = ("%s %s: %d of %d listed markets carried an outcome before their own kickoff "
                    "(%.3f, floor %.2f)" % (s, where, c["in_time"], c["n"], rate, floor))
            if rate >= floor:
                out.append(Item("emptiness", key, OK, head, detail))
                continue
            top = max(c["reasons"].items(), key=lambda kv: kv[1])
            tail = "" if pricing else (" - week %d is played: this is a hole in what was captured "
                                       "for it, not something a re-run fills" % week)
            out.append(Item("emptiness", key, RED,
                            "%s; %d mapped only after kickoff, %d never looked at, %d refused; "
                            "%smost common: %s (%d)%s"
                            % (head, c["late"], c["no_row"], c["refused"],
                               "oldest unmapped listed %s UTC; " % dt.datetime.fromtimestamp(
                                   c["oldest"], dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
                               if c["oldest"] else "", top[0], top[1], tail), detail))
    return out


# The first run of the fix leaves nothing on any venue unexamined (the pending
# pass takes every venue discovery writes), so ANY market still without a row
# after the grace is the pass not running - on any venue, in any series.
def check_unexamined(stores, now, grace_s=None):
    """Markets the mapper has never looked at: listed, and no `market_outcome`
    row at all.

    The state a census of `unmapped_reason` cannot show, because there is no
    row for the reason to be on - which is what kept a four-week mapping
    outage out of every reason list (a-68, a-73). Read from `markets`, per
    venue, RED when any is older than the grace. `check_mapped_rate` covers two
    series of one venue; this covers every market discovery has written.
    """
    grace_s = MAPPING_GRACE_S if grace_s is None else grace_s
    path = stores.get("market_log.db")
    if path is None:
        return [Item("emptiness", "unexamined", ERROR, "market_log.db not found: unexamined markets not read")]
    rows = query(path, "SELECT m.venue, COUNT(*), SUM(mo.market_id IS NULL), "
                       "SUM(mo.market_id IS NULL AND m.first_seen <= ?), "
                       "MIN(CASE WHEN mo.market_id IS NULL THEN m.first_seen END) "
                       "FROM markets m LEFT JOIN market_outcome mo "
                       "ON mo.venue = m.venue AND mo.market_id = m.market_id "
                       "GROUP BY m.venue", (now - grace_s,))
    if not rows:
        return [Item("emptiness", "unexamined", SKIPPED,
                     "markets is empty: there is nothing to have examined, which is not a pass")]
    by = {}
    for venue, n, never, stale, oldest in rows:
        v = by.setdefault(venue.split(":")[0], {"listed": 0, "unexamined": 0, "stale": 0, "oldest": None})
        v["listed"] += n
        v["unexamined"] += never or 0
        v["stale"] += stale or 0
        if oldest is not None and (v["oldest"] is None or oldest < v["oldest"]):
            v["oldest"] = oldest
    out = []
    for venue in sorted(by):
        v = by[venue]
        if v["stale"]:
            out.append(Item("emptiness", "unexamined:%s" % venue, RED,
                            "%d of %d %s markets have NO market_outcome row and were listed over "
                            "%d min ago (oldest %s UTC): the mapper has not looked at them, so no "
                            "reason census counts them"
                            % (v["stale"], v["listed"], venue, grace_s // 60,
                               dt.datetime.fromtimestamp(v["oldest"], dt.timezone.utc)
                               .strftime("%Y-%m-%d %H:%M")), v))
        else:
            out.append(Item("emptiness", "unexamined:%s" % venue, OK,
                            "%s: 0 of %d markets unexamined past %d min (%d listed inside it)"
                            % (venue, v["listed"], grace_s // 60, v["unexamined"]), v))
    return out


def depth_last_days(market_log, now, horizon):
    """{venue: last day with a market_depth row}, walking back one day at a time.

    One short connection per day. `GROUP BY venue` over the whole table does
    not finish inside 25s on the live store (measured), and a single long read
    would pin the logger's WAL for as long as it ran.
    """
    found, start = {}, time.time()
    end = (int(now) // 86400 + 1) * 86400          # midnight UTC ending today
    for _ in range(horizon):
        lo = end - 86400
        for venue, _n in query(market_log, "SELECT venue, COUNT(*) FROM market_depth "
                                           "WHERE ts >= ? AND ts < ? GROUP BY venue", (lo, end)):
            found.setdefault(venue, day(lo))
        if all(v in found for v in DEPTH_VENUES) or time.time() - start > 240:
            break
        end = lo
    return found


# --------------------------------------------------------------------------
# 3. collected and unread

def code_files(root):
    """Committed .py files outside tests. Falls back to a walk outside git."""
    try:
        r = subprocess.run(["git", "-C", root, "ls-files", "*.py"], capture_output=True, text=True)
        files = r.stdout.split("\n") if r.returncode == 0 else []
    except OSError:
        files = []
    if not [f for f in files if f]:
        files = [os.path.relpath(os.path.join(d, f), root).replace("\\", "/")
                 for d, _, fs in os.walk(root) for f in fs if f.endswith(".py")]
    return sorted(f for f in files if f and not f.startswith(("tests/", ".venv/"))
                  and "__pycache__" not in f and os.path.exists(os.path.join(root, f)))


def module_name(rel):
    return rel[:-3].replace("/", ".").removesuffix(".__init__")


def strings_of(tree):
    """Every string literal that is not a docstring, f-strings joined with {}."""
    doc = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            b = node.body
            if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant) \
                    and isinstance(b[0].value.value, str):
                doc.add(id(b[0].value))
    out, inside = [], set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            parts = []
            for v in node.values:
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    parts.append(v.value)
                    inside.add(id(v))
                else:
                    parts.append("{}")
            out.append("".join(parts))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in doc and id(node) not in inside:
            out.append(node.value)
    return out


def scan_code(root):
    """{module: {"strings": [...], "edges": {module, ...}}} for the committed tree."""
    mods, unparsed = {}, []
    files = code_files(root)
    names = {module_name(f) for f in files}
    for rel in files:
        try:
            tree = ast.parse(open(os.path.join(root, rel), encoding="utf-8").read())
        except (SyntaxError, UnicodeDecodeError):
            unparsed.append(rel)
            continue
        strs, edges = strings_of(tree), set()
        pkg = module_name(rel).rsplit(".", 1)[0] if "." in module_name(rel) else ""
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                edges.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    base = (pkg + "." + base).strip(".") if base else pkg
                edges.add(base)
                edges.update((base + "." + a.name).strip(".") for a in node.names)
        edges.update(s for s in strs if s in names)
        mods[module_name(rel)] = {"strings": strs, "edges": edges & names, "path": rel}
    return mods, unparsed


def publish_closure(root, mods):
    """Modules reachable from a publish or scheduled entry point."""
    start = {m for m in ENTRY_MODULES if m in mods}
    for f in os.listdir(root):
        if f.endswith(ENTRY_SCRIPTS):
            text = open(os.path.join(root, f), encoding="utf-8", errors="replace").read()
            start.update(m for m in re.findall(r"-m\s+([\w.]+)", text) if m in mods)
    seen, todo = set(), list(start)
    while todo:
        m = todo.pop()
        if m in seen:
            continue
        seen.add(m)
        todo.extend(mods[m]["edges"] - seen)
    return seen, start


def references(mods, name):
    """{module: set of 'read' | 'write' | 'mention'} for one table or dataset name."""
    n = re.escape(name)
    read = re.compile(r'\b(?:from|join)\s+["`\[]?%s\b' % n, re.I)
    delete = re.compile(r'\bdelete\s+from\s+["`\[]?%s\b' % n, re.I)
    write = re.compile(r'\b(?:insert(?:\s+or\s+\w+)?\s+into|replace\s+into|update|delete\s+from|'
                       r'create\s+table(?:\s+if\s+not\s+exists)?|alter\s+table)\s+["`\[]?%s\b' % n, re.I)
    word = re.compile(r"(?<![\w])%s(?![\w])" % n)
    out = {}
    for m, info in mods.items():
        kinds = set()
        for s in info["strings"]:
            if name not in s and name.lower() not in s.lower():
                continue
            if len(read.findall(s)) > len(delete.findall(s)):
                kinds.add("read")
            if write.search(s):
                kinds.add("write")
            if word.search(s):
                kinds.add("mention")
        if kinds:
            out[m] = kinds
    return out


def check_unread_tables(stores, root, mods, closure):
    out = []
    for name, path in stores.items():
        counts = {"ok": 0}
        for table in sorted(tables(path)):
            key = "%s:%s" % (name, table)
            try:
                if not query(path, 'SELECT 1 FROM "%s" LIMIT 1' % table):
                    continue                      # empty is check 2's finding
            except sqlite3.OperationalError:
                continue
            refs = references(mods, table)
            writers = sorted(m for m, k in refs.items() if "write" in k)
            readers = sorted(m for m, k in refs.items() if "read" in k and "write" not in k)
            self_read = sorted(m for m, k in refs.items() if "read" in k and "write" in k)
            mention = sorted(m for m, k in refs.items() if k == {"mention"})
            published = [m for m in readers if m in closure]
            detail = {"writers": writers, "readers": readers, "read_by_writer": self_read,
                      "mentioned": mention, "on_publish_path": published}
            if not refs:
                out.append(Item("unread", key, RED,
                                "%s holds rows and no committed code names it" % key, detail))
            elif not readers and not mention and not self_read:
                out.append(Item("unread", key, RED,
                                "%s is written by %s and read by no committed code"
                                % (key, ", ".join(writers)), detail))
            elif not readers and not mention:
                out.append(Item("unread", key, UNRULED,
                                "%s is read only inside the module that writes it (%s); this scan "
                                "cannot tell deriving from it apart from bookkeeping on it"
                                % (key, ", ".join(self_read)), detail))
            elif not readers:
                out.append(Item("unread", key, UNRULED,
                                "%s has no SQL read outside its writer; named in %s - a read through "
                                "a helper cannot be told from a write by this scan"
                                % (key, ", ".join(mention[:6])), detail))
            elif not published:
                out.append(Item("unread", key, UNRULED,
                                "%s is read by %s, none of it reachable from a publish or scheduled "
                                "entry point" % (key, ", ".join(readers[:6])), detail))
            else:
                counts["ok"] += 1
        out.append(Item("unread", "%s:tables" % name, OK,
                        "%s: %d tables with rows are read on a publish path" % (name, counts["ok"])))
    return out


def registry(root):
    """The dataset registry as data: {key: (release, filename stem, normalized)}, by AST.

    Read from the source rather than imported, so the gate needs neither httpx
    nor a configured environment to know what the registry declares.
    """
    tree = ast.parse(open(os.path.join(root, "nflverse.py"), encoding="utf-8").read())
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "DATASETS" for t in node.targets):
            for k, v in zip(node.value.keys, node.value.values):
                args = [a.value for a in v.args if isinstance(a, ast.Constant)]
                kw = {x.arg: getattr(x.value, "value", None) for x in v.keywords}
                out[k.value] = (args[1], re.sub(r"(_\{season\})?\.\w+$", "", args[2]),
                                bool(kw.get("normalize")))
    if not out:
        raise LookupError("no DATASETS registry found in nflverse.py")
    return out


def check_unread_datasets(stores, root, mods, closure):
    """Archived nflverse datasets against the code that parses them."""
    out = []
    reg = registry(root)
    archived = {d: (n, v) for d, n, v in query(
        stores["market_log.db"], "SELECT dataset, COUNT(*), MAX(data_version) FROM nflverse_versions GROUP BY 1")}
    for ds in sorted(archived):
        n, newest = archived[ds]
        release, stem, normalized = reg.get(ds, (None, ds, False))
        # By key as a word, or by filename stem as a substring: a parser names
        # the file (`depth_charts_{season}.parquet`), not the bare key.
        users = set(references(mods, ds))
        users.update(m for m, info in mods.items() if any(stem in x for x in info["strings"]))
        users = sorted(users - {"nflverse", "jobs.ingest_nflverse"})
        detail = {"files": n, "newest": newest, "normalized": normalized, "named_in": users,
                  "on_publish_path": [m for m in users if m in closure]}
        key = "nflverse:%s" % ds
        if normalized or detail["on_publish_path"]:
            out.append(Item("unread", key, OK, "%s: %s" % (key, "normalized into the store" if normalized
                                                            else "read by " + ", ".join(detail["on_publish_path"][:4])), detail))
        elif users:
            out.append(Item("unread", key, UNRULED,
                            "%s (%d files, newest %s) is named only off the publish path: %s"
                            % (key, n, newest, ", ".join(users[:6])), detail))
        else:
            out.append(Item("unread", key, RED,
                            "%s is archived (%d files, newest %s) and parsed by no committed code"
                            % (key, n, newest), detail))
    return out


def fetch_releases(url=RELEASES_URL, timeout=20):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "calibratedsports-staleness/1.0 python-urllib",
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fetched_elsewhere(mods, tag):
    """Modules outside nflverse.py that fetch release `tag` through a registry of their own.

    `feeds.sources` fetches the `injuries` release without it ever appearing in
    `nflverse.DATASETS`. The test is narrow on purpose - the tag as a string of
    its own AND an asset filename built on it in the same module - because
    `test`, `misc` and `rosters` are ordinary words that appear everywhere.
    """
    asset = re.compile(r"^%s[\w{}]*\.(parquet|csv)$" % re.escape(tag))
    return sorted(m for m, info in mods.items() if m != "nflverse"
                  and tag in info["strings"] and any(asset.match(x) for x in info["strings"]))


def check_upstream(root, mods=None, offline=False, fetch=fetch_releases):
    """What nflverse-data offers that the registry never fetches. One free call."""
    if offline:
        return [Item("unread", "upstream:nflverse-data", SKIPPED,
                     "upstream releases not compared with the registry (--offline)")]
    try:
        releases = fetch()
        offered = {r["tag_name"]: r for r in releases}
    except Exception as e:                                    # network, rate limit, shape
        return [Item("unread", "upstream:nflverse-data", ERROR,
                     "could not list nflverse-data releases (%s: %s)" % (type(e).__name__, e))]
    if not offered:
        return [Item("unread", "upstream:nflverse-data", ERROR, "release listing came back empty")]
    ours = {rel for rel, _, _ in registry(root).values()}
    out, elsewhere = [], {}
    for tag in sorted(set(offered) - ours):
        users = fetched_elsewhere(mods or {}, tag)
        if users:
            elsewhere[tag] = users
            continue
        r = offered[tag]
        assets = r.get("assets") or []
        newest = max([a.get("updated_at") or "" for a in assets] or [""])[:10]
        out.append(Item("unread", "upstream:%s" % tag, RED,
                        "nflverse release '%s' is offered and not fetched (%d assets, last updated %s)"
                        % (tag, len(assets), newest or "?"), {"assets": len(assets), "updated": newest}))
    for tag in sorted(ours - set(offered)):
        out.append(Item("unread", "upstream:%s" % tag, RED,
                        "the registry fetches release '%s', which upstream no longer lists" % tag))
    out.append(Item("unread", "upstream:nflverse-data", OK,
                    "%d upstream releases listed, %d in the registry, %d fetched by another "
                    "registry (%s), %d not fetched"
                    % (len(offered), len(ours & set(offered)), len(elsewhere),
                       ", ".join("%s by %s" % (t, "/".join(u)) for t, u in sorted(elsewhere.items())) or "none",
                       len(set(offered) - ours) - len(elsewhere)), {"elsewhere": elsewhere}))
    return out


# --------------------------------------------------------------------------
# rulings, running, rendering

RULING_FIELDS = ("check", "key", "ruled_by", "ruled_on", "reason")


def load_rulings(path=RULINGS_PATH):
    rulings = json.load(open(path, encoding="utf-8"))["rulings"]
    for r in rulings:
        missing = [f for f in RULING_FIELDS if not str(r.get(f) or "").strip()]
        if missing:
            raise ValueError("ruling %r lacks %s: a ruling names who, when and why" % (r.get("key"), missing))
    return rulings


def apply_rulings(items, rulings):
    by = {(r["check"], r["key"]): r for r in rulings}
    for it in items:
        r = by.get((it.check, it.key))
        if not r or it.status not in (RED, UNRULED):
            continue
        tol = r.get("tolerance_weeks")
        if tol is not None and not (it.detail.get("weeks_behind") is not None
                                    and it.detail["weeks_behind"] <= tol and not it.detail.get("holes")):
            continue                                   # outside what was ruled: still red
        it.statement += " [RULED by %s on %s: %s]" % (r["ruled_by"], r["ruled_on"], r["reason"])
        it.status = RULED
    return items


def find_stores(store_dir, names=NFL_STORES + OTHER_STORES):
    return {n: os.path.join(store_dir, n) for n in names if os.path.exists(os.path.join(store_dir, n))}


def _guard(label, fn, *args, **kw):
    """A check that raises is an ERROR item, never a silent absence."""
    try:
        return fn(*args, **kw)
    except Exception as e:
        return [Item(label, "%s:%s" % (label, fn.__name__), ERROR,
                     "%s did not run (%s: %s)" % (fn.__name__, type(e).__name__, e))]


def run(store_dir, root, now=None, offline=False, quick=False, strict=False,
        rulings=None, fetch=fetch_releases, grace_hours=None):
    now = time.time() if now is None else now
    stores = find_stores(store_dir)
    items = [Item("stores", n, ERROR, "store %s not found under %s" % (n, store_dir))
             for n in NFL_STORES if n not in stores]
    clock = None
    if "market_log.db" in stores:
        try:
            clock = week_clock(stores["market_log.db"], now, grace_hours)
        except Exception as e:
            items.append(Item("freshness", "clock", ERROR, "week clock not established (%s)" % e))
    if clock:
        items += _guard("freshness", check_freshness, stores, clock)
        items += _guard("freshness", check_builds, stores, clock)
        items += _guard("freshness", check_kickoff_tables, stores, clock)
        items += _guard("freshness", check_metrics, stores, clock)
        items += _guard("emptiness", check_columns, stores, clock)
    items += _guard("freshness", check_health, stores, now)
    items += _guard("emptiness", check_empty_tables, stores)
    items += _guard("emptiness", check_sport_partitions, stores)
    items += _guard("emptiness", check_series, stores, now, quick=quick)
    items += _guard("emptiness", check_mapped_rate, stores, now)
    items += _guard("emptiness", check_unexamined, stores, now)
    mods, unparsed = scan_code(root)
    closure, entries = publish_closure(root, mods)
    if unparsed or not entries:
        items.append(Item("unread", "code-scan", ERROR,
                          "code scan incomplete: %d file(s) unparsed, %d entry point(s) found"
                          % (len(unparsed), len(entries)), {"unparsed": unparsed}))
    else:
        items.append(Item("unread", "code-scan", OK,
                          "%d modules parsed, %d on a publish path from %d entry points"
                          % (len(mods), len(closure), len(entries))))
    items += _guard("unread", check_unread_tables, stores, root, mods, closure)
    if "market_log.db" in stores:
        items += _guard("unread", check_unread_datasets, stores, root, mods, closure)
    items += _guard("unread", check_upstream, root, mods, offline=offline, fetch=fetch)
    rulings = load_rulings() if rulings is None else rulings
    return GateReport(apply_rulings(items, rulings), clock, strict=strict, rulings=rulings)


TITLES = (("freshness", "1. Freshness"), ("emptiness", "2. Emptiness"),
          ("unread", "3. Collected and unread"), ("stores", "Stores"))


def render(report, now):
    lines = ["# Staleness gate", "",
             "Run %s UTC. %s." % (dt.datetime.fromtimestamp(now, dt.timezone.utc).strftime("%Y-%m-%d %H:%M"),
                                  report.clock.statement if report.clock else "WEEK CLOCK NOT ESTABLISHED"),
             "", "**%s**" % report.statement, "",
             "Rulings in force: %d. No per-table tolerance exists; a red item stays red until a "
             "ruling names it." % len(report.rulings), ""]
    for check, title in TITLES:
        mine = [i for i in report.items if i.check == check]
        if not mine:
            continue
        lines += ["## %s" % title, ""]
        for status in (RED, ERROR, UNRULED, SKIPPED, RULED):
            hit = [i for i in mine if i.status == status]
            if hit:
                lines += ["**%s (%d)**" % (status, len(hit)), ""] + ["- %s" % i.statement for i in hit] + [""]
        oks = [i for i in mine if i.status == OK]
        lines += ["ok (%d): " % len(oks) + "; ".join(i.statement for i in oks), ""]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--store-dir", help="directory holding the stores (default: config.STORAGE_DIR)")
    ap.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--offline", action="store_true", help="skip the upstream release listing")
    ap.add_argument("--quick", action="store_true", help="skip the market_depth day scan")
    ap.add_argument("--strict", action="store_true", help="UNRULED items fail too")
    ap.add_argument("--report", help="write the markdown report here")
    ap.add_argument("--json", help="write every item here")
    a = ap.parse_args(argv)
    if not a.store_dir:
        import config
        a.store_dir = config.STORAGE_DIR
    now = time.time()
    report = run(a.store_dir, a.root, now=now, offline=a.offline, quick=a.quick, strict=a.strict)
    text = render(report, now)
    if a.report:
        with open(a.report, "w", encoding="utf-8", newline="\n") as f:
            f.write(text + "\n")
    if a.json:
        with open(a.json, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"statement": report.statement, "clean": report.clean,
                       "clock": report.clock.statement if report.clock else None,
                       "items": [vars(i) for i in report.items]}, f, indent=1, default=str)
    for i in report.items:
        if i.status != OK:
            print("%-8s %-10s %s" % (i.status, i.check, i.statement))
    print(report.statement)
    return 0 if report.clean else 1


if __name__ == "__main__":
    sys.exit(main())
