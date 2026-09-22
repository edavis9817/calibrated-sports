"""MLB stats from Retrosheet: games, team-games, player-games. Stats only, 0 credits.

    python -m jobs.ingest_mlb                          # status, 0 requests
    python -m jobs.ingest_mlb --fetch 1999-2025        # one request per season + notice
    python -m jobs.ingest_mlb --parse 1999-2025        # replay the archive, 0 requests
    python -m jobs.ingest_mlb --reconcile 2025         # do the players sum to the team?
    python -m jobs.ingest_mlb --totals 2025 --player ohtas001
    python -m jobs.ingest_mlb --audit                  # manifest vs disk, and readable

Status, --totals and --audit are READ-ONLY (`connect_ro`: mode=ro + query_only); only
--fetch, --parse and --reconcile open the store for writing (c-07).

STATS ONLY. No odds, no props, no market data, no credits: odds for MLB are a later and
deliberate decision (c-03). `mlb.sources.check_url` refuses every host but Retrosheet.

WHAT WAS PUBLISHED, WHEN WE READ IT. Retrosheet re-publishes finished seasons (the 2024
bundle was re-dated 2026-08-07), so every row is versioned by INGESTION time through
`cfb.versioning.apply` - the same implementation CFB and feeds use - and a restatement
closes the old row rather than overwriting it. A re-fetch whose members are unchanged
keeps the earlier archive file (`feeds.fetch.archive`, content hash over the members).

NO CURRENT SEASON. Retrosheet publishes a season after it ends; `2026csvs.zip` is a 404
(measured 2026-09-22). The only free in-season feed is MLBAM's, and its terms forbid
bulk use. That is a limitation row, not a TODO.

PUBLISHES NOTHING. There is no upload path. `jobs.export_mlb_web` is a local-only probe of
whether the contract admits this sport.
"""
import argparse
import gzip
import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx                                                    # noqa: E402

from cfb import versioning                                      # noqa: E402
from core.single_instance import AlreadyRunning, InstanceLock   # noqa: E402
from feeds import fetch as raw                                  # noqa: E402
from mlb import normalize, paths, schema, sources, totals       # noqa: E402

USER_AGENT = "calibrated-sports-mlb (contact: github.com/edavis9817/calibrated-sports)"
GAP_S = 2.0          # between season downloads; Retrosheet is a volunteer project
MANIFEST = "mlb_raw_files"

LIMITATIONS = [
    ("mlb.no_current_season", "coverage",
     "There is no current-season MLB data",
     "Retrosheet publishes a season's files only after the season ends: 2025 is the "
     "newest bundle and 2026csvs.zip returned 404 on 2026-09-22. The only free in-season "
     "feed is MLB's Stats API, whose terms permit 'only individual, non-commercial, "
     "non-bulk use'.",
     "Everything this store says about MLB is historical. A page must not present the "
     "newest season held as the current one. In-season stats need either a licensed "
     "feed or written permission from MLBAM - a decision, not an engineering task."),
    # CORRECTED c-07. The c-03 text said "Any export of MLB data carries the statement";
    # f-03 counted it in 0 of 4,757 exported files. What is true is below, and
    # tests/test_ingest_mlb.py fails if the contract gains the field while this row still
    # says it has none - the row is pinned to the state it describes.
    ("mlb.attribution_required", "licence",
     "Anything published from this store must carry Retrosheet's statement",
     "Retrosheet's notice permits any use, commercial included, on one condition: the "
     "statement in mlb.sources.ATTRIBUTION 'must appear prominently'.",
     "NOT YET MET IN ANY JSON FILE, SO NO MLB DATA MAY BE PUBLISHED. The contract has no "
     "field for it and every object is closed, so no exported JSON file carries the "
     "statement; the probe export writes it only as mlb/NOTICE.txt beside the tree, which "
     "the uploader never sends. The site does not render it. jobs.export_mlb_web refuses "
     "to write into WEB_EXPORT_DIR until the sport manifest carries it. Filed: the field "
     "to track A (A-C10); rendering it, and naming Retrosheet as the source, to track B."),
    ("mlb.no_league_or_division", "coverage",
     "The bundle carries no league or division",
     "gameinfo, teamstats and allplayers name teams by Retrosheet code only. League and "
     "division are not in these files.",
     "A team listing from this store has null conference and division - an honest "
     "null, not an inferred grouping."),
    ("mlb.retrosheet_ids_only", "identity",
     "Players are keyed by Retrosheet id only",
     "No MLBAM, Baseball-Reference or FanGraphs id is ingested. The Chadwick Bureau "
     "register (ODC-BY) carries that crosswalk and is not ingested yet.",
     "Joining to any other MLB source waits on the crosswalk, ingested at ingest time as "
     "the NFL invariant requires, never in analysis code."),
    ("mlb.fielding_and_plays_not_parsed", "coverage",
     "Fielding lines and play-by-play are archived, not parsed",
     "Each bundle's fielding.csv (per position per game) and plays.csv (~108 MB per "
     "season uncompressed) are in the raw archive and in no table.",
     "Re-derivable from the archive at zero requests when a use appears."),
]


def connect(db_path=None):
    """The WRITE connection: DDL, WAL, and the limitation rows. Only the paths that write
    facts use it (--fetch, --parse, --reconcile). A read goes through `connect_ro`."""
    db = db_path or paths.db_path()
    os.makedirs(os.path.dirname(db), exist_ok=True)
    con = sqlite3.connect(db, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(schema.ddl())
    # `recorded_ts` is when the STATEMENT last changed, not when someone last connected:
    # an unchanged row is left alone (c-07; it used to be rewritten on every connect,
    # reads included, so the column meant "last opened").
    for lid, sev, title, statement, consequence in LIMITATIONS:
        con.execute(
            "INSERT INTO mlb_limitations VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "severity=excluded.severity, title=excluded.title, statement=excluded.statement, "
            "consequence=excluded.consequence, recorded_ts=excluded.recorded_ts "
            "WHERE mlb_limitations.severity IS NOT excluded.severity "
            "OR mlb_limitations.title IS NOT excluded.title "
            "OR mlb_limitations.statement IS NOT excluded.statement "
            "OR mlb_limitations.consequence IS NOT excluded.consequence",
            (lid, sev, title, statement, consequence, time.time()))
    con.commit()
    return con


class NoStore(Exception):
    pass


def connect_ro(db_path=None):
    """A READ connection that cannot write: `mode=ro` on the URI, and `query_only` on top.
    It runs no DDL and creates no file - a missing store is an error, not an empty store.

    Why (c-07, from f-03): --totals, --audit and the status line went through `connect()`,
    which runs DDL, switches WAL on and rewrote every limitation's `recorded_ts`. A read
    that opens the store read-write is the `map_markets --coverage` defect, which has
    already bitten this project once. `mode=ro` on a WAL database may still leave
    `-wal`/`-shm` side files - SQLite's shared-memory index, not a write."""
    db = db_path or paths.db_path()
    if not os.path.exists(db):
        raise NoStore(f"no MLB store at {db} - nothing to read (run --fetch first)")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30)
    con.execute("PRAGMA query_only = ON")
    return con


def measure(con, key, scope, value, detail=None):
    con.execute("INSERT INTO mlb_measurements (key, scope, value, detail, measured_ts) "
                "VALUES (?,?,?,?,?) ON CONFLICT(key, scope) DO UPDATE SET "
                "value=excluded.value, detail=excluded.detail, measured_ts=excluded.measured_ts",
                (key, str(scope), value, detail, time.time()))
    con.commit()


class Client:
    """Sequential, gapped, stops on 403/429 rather than retrying. Every URL passes
    `sources.check_url` first, so this client cannot reach a refused host."""

    def __init__(self, con, http=None):
        self.con = con
        self.http = http or httpx.Client(follow_redirects=False, timeout=300,
                                         headers={"User-Agent": USER_AGENT})
        self._last = 0.0

    def get(self, url):
        sources.check_url(url)
        wait = GAP_S - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        try:
            r = self.http.get(url)
        except httpx.HTTPError as e:
            self._log(url, None, None, f"error: {type(e).__name__}")
            raise raw.FetchError(f"{url}: {type(e).__name__}") from None
        finally:
            self._last = time.time()
        self._log(url, r.status_code, len(r.content),
                  "ok" if r.status_code == 200 else f"http_{r.status_code}")
        if r.status_code in (403, 429):
            raise raw.FetchError(f"{r.status_code} from {url} - stopping rather than retrying")
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise raw.FetchError(f"{r.status_code} from {url}")
        return r.content

    def _log(self, url, status, nbytes, outcome):
        self.con.execute("INSERT INTO mlb_http_log VALUES (?,?,?,?,?)",
                         (time.time(), url, status, nbytes, outcome))
        self.con.commit()


def _archive(con, feed, scope, url, body, kind, suffix):
    return raw.archive(con, feed, scope, url, body, kind=kind, suffix=suffix,
                       raw_root=paths.raw_root(), manifest=MANIFEST)


def run_fetch(con, seasons, client=None, verbose=True):
    """Download, archive verbatim, then parse. The terms are archived with the data."""
    client = client or Client(con)
    notice = client.get(sources.NOTICE_URL)
    if notice is None:
        raise raw.FetchError("Retrosheet notice.txt is 404 - refusing to ingest without "
                             "the terms that permit it")
    if b"free to make any" not in notice:
        raise raw.FetchError("Retrosheet notice.txt no longer contains the permission "
                             "this ingest relies on - re-read the terms before fetching")
    _archive(con, "retrosheet_notice", None, sources.NOTICE_URL, notice, "bytes", ".txt.gz")
    out = {}
    for season in seasons:
        url = sources.season_url(season)
        body = client.get(url)
        if body is None:
            out[season] = "absent (404)"
            if verbose:
                print(f"  {season}: absent (404)")
            continue
        file_id, outcome = _archive(con, sources.FEED, season, url, body, "zip", ".zip.gz")
        out[season] = f"{outcome} " + parse_file(con, season, file_id, body, verbose=False)
        if verbose:
            print(f"  {season}: {len(body) / 1e6:.1f} MB  {out[season]}")
    return out


def newest_file(con, season):
    return con.execute(f"SELECT file_id FROM {MANIFEST} WHERE feed=? AND scope=? "
                       f"ORDER BY fetched_ts DESC LIMIT 1",
                       (sources.FEED, str(season))).fetchone()


def parse_file(con, season, file_id, body=None, verbose=True):
    body = body if body is not None else raw.read_archived(
        con, file_id, raw_root=paths.raw_root(), manifest=MANIFEST)
    parsed = normalize.bundle(body, season)
    parts = []
    for member, (table, rows, meas) in parsed.items():
        ts = time.time()
        con.execute("BEGIN")
        ins, closed, same = versioning.apply(con, sources.FEED, season, file_id, ts, table,
                                             rows, label=f"{season} {member}",
                                             schema_mod=schema)
        con.execute("INSERT INTO mlb_parse_log VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (ts, f"{season} {member}", file_id, table, len(rows), ins, closed,
                     same, "ok", json.dumps(meas["stattypes"]) if meas["stattypes"] else None))
        con.commit()
        measure(con, f"{table}.rows", season, len(rows),
                json.dumps(meas["stattypes"]) if meas["stattypes"] else None)
        parts.append(f"{member} +{ins} -{closed} ={same}")
    line = "  ".join(parts)
    if verbose:
        print(f"  {season}: {line}")
    return line


def run_parse(con, seasons, verbose=True):
    out = {}
    for season in seasons:
        row = newest_file(con, season)
        if row is None:
            out[season] = "not archived"
            if verbose:
                print(f"  {season}: not archived")
            continue
        out[season] = parse_file(con, season, row[0], verbose=verbose)
    return out


# ---------------------------------------------------------------------------
# reads: season totals are computed, never stored - and folded by mlb.totals
# ---------------------------------------------------------------------------

CURRENT = "valid_to_ts IS NULL AND stattype = 'value'"

# The aggregation rule - NULL is contagious, a tiebreaker is regular season - lives in
# `mlb.totals` and nowhere else. Re-exported here because callers import it from here.
REGULAR_SEASON = totals.REGULAR_SEASON


def season_totals(con, season, kind="batting", gametypes=REGULAR_SEASON, player_id=None):
    """Per player: games, teams, and the total of every component, as of now.

    SQL SELECTS the lines; `mlb.totals` FOLDS them. There is no SQL aggregate here on
    purpose: that was the second copy of the rule (c-07). `teams` is the sorted,
    comma-joined set of teams the player had a line for."""
    table = {"batting": "mlb_batting", "pitching": "mlb_pitching"}[kind]
    stats = schema.BAT_STATS + schema.BAT_FLAGS if kind == "batting" else \
        schema.PIT_STATS + schema.PIT_FLAGS
    where = f"{CURRENT} AND season = ? AND gametype IN ({','.join('?' * len(gametypes))})"
    params = [season, *gametypes]
    if player_id:
        where += " AND player_id = ?"
        params.append(player_id)
    cur = con.execute(f"SELECT player_id, game_id, team, {', '.join(stats)} FROM {table} "
                      f"WHERE {where}", params)
    acc = {}
    for pid, gid, team, *vals in cur:
        a = acc.get(pid)
        if a is None:
            a = acc[pid] = {"games": set(), "teams": set(), "stats": {}}
        a["games"].add(gid)
        a["teams"].add(team)
        totals.fold_into(a["stats"], dict(zip(stats, vals)))
    return [{"player_id": pid, "games": len(a["games"]), "teams": ",".join(sorted(a["teams"])),
             **a["stats"]} for pid, a in sorted(acc.items())]


def team_records(con, season, gametypes=REGULAR_SEASON):
    """{team: (games, wins, losses, ties)} from the team lines, folded by `mlb.totals`."""
    out = {}
    for team, win, loss, tie in con.execute(
            f"SELECT team, win, loss, tie FROM mlb_team_games WHERE {CURRENT} AND season = ? "
            f"AND gametype IN ({','.join('?' * len(gametypes))}) ORDER BY team",
            (season, *gametypes)):
        g, w, l, t = out.get(team, (0, 0, 0, 0))
        out[team] = (g + 1, totals.add(w, win), totals.add(l, loss), totals.add(t, tie))
    return out


def reconcile(con, season):
    """Do the parts sum to the whole? Player batting and pitching lines, summed per
    (game, team), against the team's own line - for every component, every game.
    Returns {component: games where they differ}; also the game-count cross-check."""
    # NOT a published total, so it does not go through `mlb.totals`: the SQL SUM here
    # skips NULL on purpose - a partial player sum then differs from the team line and is
    # COUNTED, which is the detection. Nothing this returns is published as a stat.
    out = {}
    for table, stats in (("mlb_batting", schema.BAT_STATS),
                         ("mlb_pitching", schema.PIT_STATS)):
        sums = ", ".join(f"SUM(p.{c}) AS {c}" for c in stats)
        diffs = ", ".join(f"SUM(CASE WHEN t.{c} IS NOT s.{c} THEN 1 ELSE 0 END)" for c in stats)
        row = con.execute(
            f"WITH s AS (SELECT p.game_id, p.team, {sums} FROM {table} p "
            f"WHERE p.valid_to_ts IS NULL AND p.stattype='value' AND p.season=? "
            f"GROUP BY p.game_id, p.team) "
            f"SELECT COUNT(*), {diffs} FROM s JOIN mlb_team_games t "
            f"ON t.game_id=s.game_id AND t.team=s.team AND t.valid_to_ts IS NULL "
            f"AND t.stattype='value'", (season,)).fetchone()
        out[f"{table}.team_games_joined"] = row[0]
        for c, n in zip(stats, row[1:]):
            out[f"{table}.{c}"] = n
    out["games"] = con.execute(
        "SELECT COUNT(*) FROM mlb_games WHERE valid_to_ts IS NULL AND season=?",
        (season,)).fetchone()[0]
    out["team_games"] = con.execute(
        f"SELECT COUNT(*) FROM mlb_team_games WHERE {CURRENT} AND season=?",
        (season,)).fetchone()[0]
    return out


# ---------------------------------------------------------------------------
# audit, status, CLI
# ---------------------------------------------------------------------------

def audit(con):
    """Manifest vs disk, and every manifested file must open AND decompress fully -
    the check the NFL shard audit lacked (CLAUDE.md, two processes one shard)."""
    root = paths.raw_root()
    on_disk = set()
    for dirpath, _d, files in os.walk(root):
        for f in files:
            on_disk.add(os.path.relpath(os.path.join(dirpath, f), root).replace(os.sep, "/"))
    manifest = {r[0]: r[1] for r in con.execute(
        f"SELECT rel_path, bytes_sha256 FROM {MANIFEST}")}
    import hashlib
    bad = []
    for rel in sorted(set(manifest) & on_disk):
        try:
            with gzip.open(os.path.join(root, *rel.split("/")), "rb") as f:
                body = f.read()
            if hashlib.sha256(body).hexdigest() != manifest[rel]:
                bad.append((rel, "sha256 differs from manifest"))
        except Exception as e:
            bad.append((rel, f"{type(e).__name__}: {e}"))
    unreg, missing = on_disk - set(manifest), set(manifest) - on_disk
    statement = (f"mlb raw audit: {len(on_disk)} on disk, {len(manifest)} manifested, "
                 f"unregistered {len(unreg)}, missing {len(missing)}, unreadable or "
                 f"altered {len(bad)}")
    print(f"  {statement}")
    for rel, why in bad[:10]:
        print(f"    BAD {rel}: {why}")
    return {"statement": statement, "clean": not unreg and not missing and not bad,
            "bad": bad}


def status(con):
    print(f"mlb store  db={paths.db_path()}")
    for table in schema.TABLES:
        n = con.execute(f"SELECT COUNT(*) FROM {table} WHERE valid_to_ts IS NULL").fetchone()[0]
        allv = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        seasons = con.execute(f"SELECT MIN(src_season), MAX(src_season), "
                              f"COUNT(DISTINCT src_season) FROM {table}").fetchone()
        print(f"  {table:18} current {n:>10,}  all versions {allv:>10,}  seasons {seasons}")
    files, nbytes = con.execute(
        f"SELECT COUNT(*), COALESCE(SUM(bytes), 0) FROM {MANIFEST}").fetchone()
    print(f"  raw files {files}  {nbytes / 1e6:.1f} MB")
    print("  limitations:")
    for lid, title in con.execute("SELECT id, title FROM mlb_limitations ORDER BY id"):
        print(f"    {lid:36} {title}")


def print_totals(con, a):
    rows = season_totals(con, a.totals, a.kind, player_id=a.player)
    if not rows:
        print(f"  no {a.kind} rows for {a.totals} {a.player or ''}")
        return 1
    for r in rows[:25]:
        print("  " + json.dumps(r))
    print(f"  {len(rows)} players")
    return 0


def parse_seasons(spec):
    out = set()
    for part in str(spec).split(","):
        if "-" in part:
            a, b = part.split("-")
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return sorted(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fetch", metavar="SEASONS", help="2025 | 1999-2025")
    ap.add_argument("--parse", metavar="SEASONS", help="replay the archive, 0 requests")
    ap.add_argument("--reconcile", metavar="SEASONS")
    ap.add_argument("--totals", metavar="SEASON", type=int)
    ap.add_argument("--player")
    ap.add_argument("--kind", default="batting", choices=("batting", "pitching"))
    ap.add_argument("--audit", action="store_true")
    a = ap.parse_args(argv)

    # READS, read-only: the status line, --totals and --audit never open the store for
    # writing (c-07). `connect_ro` refuses a missing store rather than creating one.
    if not any((a.fetch, a.parse, a.reconcile)):
        try:
            if a.audit:
                paths.ensure_dirs()                     # the lock's directory, not the store
                with InstanceLock(paths.lock_path()):   # never audit a fetch mid-write
                    return 0 if audit(connect_ro())["clean"] else 1
            con = connect_ro()
        except NoStore as e:
            print(f"REFUSING: {e}")
            return 1
        except AlreadyRunning as e:
            print(f"REFUSING: {e}")
            return 2
        if a.totals:
            return print_totals(con, a)
        status(con)
        return 0
    paths.ensure_dirs()
    try:
        with InstanceLock(paths.lock_path()):
            con = connect()
            if a.fetch:
                run_fetch(con, parse_seasons(a.fetch))
            if a.parse:
                run_parse(con, parse_seasons(a.parse))
            rc = 0
            if a.reconcile:
                for season in parse_seasons(a.reconcile):
                    r = reconcile(con, season)
                    off = {k: v for k, v in r.items() if v and "." in k
                           and not k.endswith("team_games_joined")}
                    measure(con, "reconcile.components_differing", season, len(off),
                            json.dumps(r, sort_keys=True))
                    print(f"  {season}: games {r['games']}  team lines {r['team_games']}  "
                          f"joined bat {r['mlb_batting.team_games_joined']} "
                          f"pit {r['mlb_pitching.team_games_joined']}  "
                          f"components differing: {off or 'none'}")
                    if r["team_games"] == 0:
                        print(f"  {season}: NOTHING TO RECONCILE - no team lines held")
                        rc = 1
            if a.totals:
                rc = print_totals(con, a) or rc
            return rc
    except AlreadyRunning as e:
        print(f"REFUSING: {e}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
