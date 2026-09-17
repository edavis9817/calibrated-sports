"""W07 track C: SURVEY the CFB play-by-play feeds. Read only; nothing is ingested.

    python -m research.cfb_pbp_survey --download        # GitHub release parquet -> cfb/cache (unmetered, 0 credits)
    python -m research.cfb_pbp_survey --scan            # column coverage per season -> cfb.db
    python -m research.cfb_pbp_survey --report          # feeds, seasons, rows, coverage cliffs
    python -m research.cfb_pbp_survey --silent-zeros    # non-null and exactly zero for a run of seasons
    python -m research.cfb_pbp_survey --column yards_gained [--feed espn_cfb_pbp]

WHY A SURVEY FIRST. Track F's NFL survey (`docs/F01-pbp-survey.md`) found three
defects that would have shipped as confident numbers: targets unreconstructable
2003-08, `qb_hit` exactly 0.000 for three seasons while non-null, and a tackle
definition migrating mid-archive. The same shapes are likelier here, not less:
college coverage is thinner and the feeds are assembled from different upstreams.

THE MACHINERY IS TRACK F'S AND IS NOT COPIED. `analytics.survey.scan_season`
measures a parquet; `profiles`, `reference`, `anomalies`, `silent_zeros` and the
formatters read the rows back. This module supplies only what is CFB-specific:
which release assets exist, where they are cached, and that the rows land in
`cfb.db` rather than `analytics.db`. The one thing it cannot reuse is
`analytics.survey.run_scan`, which enumerates seasons through the nflverse
registry and mirror layout - see FEEDS below and the mismatch note in the report.

TWO KINDS OF EMPTY, as Track F defines them: `null` is loud (a mean skips it),
`zero` is silent (a mean returns a wrong number). Counts are stored, never rates.

WHAT IS NOT DONE HERE: no table of plays, no derived metric, no page. The
downloads land in `cfb/cache/pbp_survey/`, OUTSIDE the manifested raw archive,
because a survey copy is not an ingest and must not look like one.
"""
import argparse
import hashlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

from analytics import survey
from cfb import paths

REPO = "sportsdataverse/sportsdataverse-data"
USER_AGENT = "calibrated-sports-cfb-ingest"
GAP_S = 0.5

# feed -> (release tag, asset pattern). Both are seasonal parquet.
#   cfbfastR_cfb_pbp  the cfbfastR build (CFBD-derived), 2014-2026
#   espn_cfb_pbp      ESPN's own play feed, 2004-2026 - nine seasons deeper
FEEDS = {
    "cfbfastR_cfb_pbp": ("cfbfastR_cfb_pbp", "play_by_play_{season}.parquet"),
    "espn_cfb_pbp": ("espn_cfb_pbp", "play_by_play_{season}.parquet"),
}
DEFAULT_FEED = "cfbfastR_cfb_pbp"


def cache_root(feed=None):
    return paths.root("cache", "pbp_survey", *( [feed] if feed else [] ))


def release_assets(client, tag):
    r = client.get(f"https://api.github.com/repos/{REPO}/releases/tags/{tag}",
                   headers={"Accept": "application/vnd.github+json"})
    if r.status_code in (403, 429):
        raise SystemExit(f"GitHub {r.status_code} listing {tag}: {r.text[:200]}")
    r.raise_for_status()
    return {a["name"]: a for a in (r.json().get("assets") or [])}


def season_of(name):
    digits = "".join(ch if ch.isdigit() else " " for ch in name).split()
    for d in digits:
        if len(d) == 4 and d.startswith("20"):
            return int(d)
    return None


def download(feeds=None, verbose=True):
    """Parquet only, to the cache. Skips a file already present at the listed size."""
    got = []
    with httpx.Client(follow_redirects=True, timeout=300,
                      headers={"User-Agent": USER_AGENT}) as client:
        for feed in (feeds or FEEDS):
            tag, _pattern = FEEDS[feed]
            assets = release_assets(client, tag)
            os.makedirs(cache_root(feed), exist_ok=True)
            for name, a in sorted(assets.items()):
                if not name.endswith(".parquet"):
                    continue
                dest = os.path.join(cache_root(feed), name)
                if os.path.exists(dest) and os.path.getsize(dest) == a["size"]:
                    got.append((feed, name, "cached"))
                    continue
                t0 = time.time()
                h = hashlib.sha256()
                n = 0
                with client.stream("GET", a["browser_download_url"]) as r:
                    r.raise_for_status()
                    with open(dest + ".part", "wb") as f:
                        for chunk in r.iter_bytes(1 << 20):
                            f.write(chunk)
                            h.update(chunk)
                            n += len(chunk)
                if n != a["size"]:
                    os.remove(dest + ".part")
                    raise SystemExit(f"{name}: got {n} bytes, listing says {a['size']}")
                os.replace(dest + ".part", dest)
                with open(dest + ".sha256", "w") as f:
                    json.dump({"sha256": h.hexdigest(), "bytes": n,
                               "remote_updated_at": a["updated_at"],
                               "fetched_ts": time.time()}, f)
                got.append((feed, name, "new"))
                if verbose:
                    print(f"  {feed:18} {name:28} {n / 1e6:7.1f} MB  {time.time() - t0:5.1f}s",
                          flush=True)
                time.sleep(GAP_S)
    return got


def cached_files(feed):
    root = cache_root(feed)
    if not os.path.isdir(root):
        return []
    out = []
    for name in sorted(os.listdir(root)):
        if name.endswith(".parquet"):
            season = season_of(name)
            if season:
                out.append((season, os.path.join(root, name)))
    return out


def scan(feeds=None, seasons=None, verbose=True):
    """Track F's `scan_season` per file; rows land in cfb.db under Track F's schema."""
    con = __import__("sqlite3").connect(paths.db_path(), timeout=30)
    con.executescript(survey.SCHEMA)
    now = int(time.time())
    stats = {"feeds": 0, "seasons": 0, "rows": 0}
    for feed in (feeds or FEEDS):
        files = [f for f in cached_files(feed) if not seasons or f[0] in seasons]
        if not files:
            raise SystemExit(f"no cached {feed} parquet - run --download first. "
                             f"A scan that reads nothing and exits 0 is what this prevents.")
        stats["feeds"] += 1
        for season, path in files:
            t0 = time.time()
            rows, games, cols = survey.scan_season(path)
            pull = time.strftime("%Y-%m-%d", time.gmtime(os.path.getmtime(path)))
            con.execute("INSERT OR REPLACE INTO f_pbp_files VALUES (?,?,?,?,?,?,?,?,?)",
                        (feed, season, path, pull, rows, len(cols),
                         os.path.getsize(path), games, now))
            con.execute("DELETE FROM f_pbp_columns WHERE dataset=? AND season=?", (feed, season))
            # Named columns, not positional: Track F's table is theirs to extend, and a
            # column added there (a `condition` discriminator is already in their working
            # copy) must not silently shift this insert's values by one.
            con.executemany(
                "INSERT INTO f_pbp_columns (dataset, season, column_name, dtype, rows, nonnull, "
                "informative, distinct_n, pull_date, scanned_ts) VALUES (?,?,?,?,?,?,?,?,?,?)",
                [(feed, season, c, d, rows, nn, inf, dn, pull, now)
                 for c, d, nn, inf, dn in cols])
            con.commit()
            stats["seasons"] += 1
            stats["rows"] += rows
            if verbose:
                print(f"  {feed:18} {season}  rows {rows:>9,}  games {games or 0:>5}  "
                      f"cols {len(cols):>4}  {time.time() - t0:5.1f}s", flush=True)
    con.close()
    return stats


def _con():
    import sqlite3
    return sqlite3.connect(f"file:{paths.db_path()}?mode=ro", uri=True)


def report(feeds=None):
    con = _con()
    print("FEEDS (cached and scanned)")
    for feed, season, rows, cols, nbytes, games in con.execute(
            "SELECT dataset, season, rows, columns_n, bytes, games FROM f_pbp_files "
            "ORDER BY dataset, season"):
        print(f"  {feed:18} {season}  rows {rows:>9,}  games {games or 0:>5}  cols {cols:>4}  "
              f"{nbytes / 1e6:6.1f} MB")
    for feed in (feeds or FEEDS):
        n = con.execute("SELECT COUNT(*) FROM f_pbp_files WHERE dataset=?", (feed,)).fetchone()[0]
        if not n:
            continue
        found = survey.anomalies(con, dataset=feed)
        print(f"\n{feed}: {len(found)} columns with a coverage cliff")
        print(survey.format_anomalies(found, steps=True))
    con.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--silent-zeros", action="store_true")
    ap.add_argument("--column")
    ap.add_argument("--feed", action="append", choices=sorted(FEEDS))
    ap.add_argument("--season", type=int, action="append")
    a = ap.parse_args(argv)
    feeds = a.feed or None
    if a.download:
        got = download(feeds)
        print(f"download: {sum(1 for g in got if g[2] == 'new')} new, "
              f"{sum(1 for g in got if g[2] == 'cached')} already cached")
    if a.scan:
        print(f"scan: {scan(feeds, set(a.season) if a.season else None)}")
    if a.column:
        con = _con()
        for feed in (feeds or FEEDS):
            rows = survey.coverage(con, a.column, dataset=feed)
            if rows:
                print(f"{feed} {a.column}")
                for season, n, nn, inf, dn, dt in rows:
                    print(f"  {season}  rows {n:>9,}  non-null {nn / n if n else 0:6.3f}  "
                          f"informative {inf / n if n else 0:6.3f}  distinct {dn:>7}  {dt}")
        con.close()
    if a.silent_zeros:
        con = _con()
        for feed in (feeds or FEEDS):
            rows = survey.silent_zeros(con, dataset=feed)
            print(f"\n{feed}: {len(rows)} silent-zero runs")
            print(survey.format_silent_zeros(rows))
        con.close()
    if a.report:
        report(feeds)
    if not any((a.download, a.scan, a.report, a.silent_zeros, a.column)):
        ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
