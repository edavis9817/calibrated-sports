"""f-16 - which number is current, what R11 needs from week 2, and whether player
pages are refreshed. Read-only everywhere: the logger store through
`analytics.paths.market_log_ro()` (mode=ro), the export tree by reading files,
git history by `git show`.

    python -m research.f16_sources_of_truth --populations
    python -m research.f16_sources_of_truth --h1-inputs
    python -m research.f16_sources_of_truth --freshness --export-dir D:/.../web_export

--populations  The walk-forward table as CLAUDE.md carried it at the commit that
               first published it (06d26eb, pre-settlement-fix) and as it carries
               it now, per season and summed. The brief's "14,857 over 813" and the
               site's "6,031 over 284" are reconciled from these rows, not recalled.
--h1-inputs    What `research.sweep.h1_settlement` would read for week 2: final
               scores, the settled-market archive shards, play-by-play by week, and
               order-book depth by ticker date. H1's registered figure is a mean
               over DEPTH-CONFIRMED observations, so depth coverage decides whether
               a week-2 replication can populate it at all.
--freshness    Every player's 2026 season file: its generated_at day, the last
               regular-season week it carries, and whether every player with a
               week-2 stat row has week 2 in the file.

Each part asserts on the shape of what it read and exits non-zero on an empty
read, because an empty table here is indistinguishable from a clean answer.
"""
import argparse
import collections
import datetime as dt
import glob
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics import paths  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIRST_PUBLISHED = "06d26eb"   # 023 Part 1: the model loses to the sportsbook close
SEASON_ROW = re.compile(r"^\+?\s+(20\d\d)\s+([\d,]+)\s+(\d+)\s+([\d.]+) / ([\d.]+) / ([\d.]+)\s+"
                        r"([+-][\d.]+) \[([+-][\d.]+),\s*([+-][\d.]+)\]")
SERIES = ("KXNFLREC", "KXNFLRSHATT", "KXNFLSPREAD")
WEEK2_DATES = ("26SEP17", "26SEP18", "26SEP19", "26SEP20", "26SEP21", "26SEP22")


def fail(msg):
    sys.exit("REFUSED: " + msg)


def utc(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%m-%d %H:%MZ") if ts else "-"


# ---------------------------------------------------------------------------
def walkforward_rows(rev):
    text = subprocess.run(["git", "show", f"{rev}:CLAUDE.md"], cwd=ROOT,
                          capture_output=True, text=True, encoding="utf-8").stdout
    start = text.find("THE MODEL LOSES TO THE SPORTSBOOK CLOSE")
    if start < 0:
        fail(f"{rev}: no walk-forward section in CLAUDE.md")
    rows = []
    for line in text[start:start + 3000].splitlines():
        m = SEASON_ROW.match(line)
        if m:
            rows.append((int(m[1]), int(m[2].replace(",", "")), int(m[3]), float(m[4]), float(m[5]),
                         float(m[6]), float(m[7]), float(m[8]), float(m[9])))
    if len(rows) != 3:
        fail(f"{rev}: expected 3 season rows, parsed {len(rows)}")
    return rows


def populations():
    for label, rev in (("first published, pre-settlement-fix", FIRST_PUBLISHED), ("current HEAD", "HEAD")):
        rows = walkforward_rows(rev)
        print(f"\n{label} ({rev})")
        print("  season      n  games  Brier model/close/naive    model - close")
        for s, n, g, bm, bc, bn, est, lo, hi in rows:
            print(f"  {s}  {n:>6,}  {g:>5}  {bm:.4f}/{bc:.4f}/{bn:.4f}  {est:+.4f} [{lo:+.4f}, {hi:+.4f}]")
        print(f"  pooled {sum(r[1] for r in rows):>6,}  {sum(r[2] for r in rows):>5}   (sum of the three seasons)")


# ---------------------------------------------------------------------------
def h1_inputs():
    c = paths.market_log_ro()
    g = c.execute("""SELECT COUNT(*), SUM(home_score IS NOT NULL) FROM nfl_games x WHERE season=2026 AND week=2
                     AND data_version=(SELECT MAX(data_version) FROM nfl_games h WHERE h.game_id=x.game_id)""").fetchone()
    print(f"week-2 games {g[0]}, with a final score {g[1]}")
    if not g[0]:
        fail("no week-2 games in nfl_games")
    shards = c.execute("SELECT venue, day, state, deleted_local_ts FROM raw_shards "
                       "WHERE venue LIKE 'kalshi_settled%' ORDER BY venue, day").fetchall()
    print(f"settled-market archive shards: {len(shards)}")
    for v, day, state, deleted in shards:
        print(f"  {v:<26} day {day}  state {state}  deleted locally {'yes' if deleted else 'no'}")
    print("\norder-book depth by ticker date (rows / markets / first..last snapshot):")
    total = 0
    for s in SERIES:
        per = collections.defaultdict(lambda: [0, 0, None, None])
        for mid, n, lo, hi in c.execute(
                "SELECT market_id, COUNT(*), MIN(ts), MAX(ts) FROM market_depth WHERE venue='kalshi' "
                "AND market_id >= ? AND market_id < ? GROUP BY market_id", (s + "-26SEP", s + "-26SEP\uffff")):
            d = per[mid.split("-")[1][:7]]
            d[0] += n
            d[1] += 1
            d[2] = lo if d[2] is None else min(d[2], lo)
            d[3] = hi if d[3] is None else max(d[3], hi)
            total += n
        for day in sorted(per):
            n, k, lo, hi = per[day]
            tag = "  <- week 2" if day in WEEK2_DATES else ""
            print(f"  {s:<12} {day}  {n:>9,} rows  {k:>5} markets  {utc(lo)} .. {utc(hi)}{tag}")
        mk = c.execute("SELECT COUNT(*) FROM markets WHERE venue='kalshi' AND market_id >= ? AND market_id < ?",
                       (s + "-26SEP20", s + "-26SEP20\uffff")).fetchone()[0]
        print(f"  {s:<12} 26SEP20 markets listed: {mk}")
    c.close()
    if not total:
        fail("no depth rows at all - the read is empty, not the answer")
    import polars as pl
    hit = paths.latest_asset("play_by_play_2026.parquet")
    if not hit:
        fail("no 2026 play-by-play in the mirror")
    f, day = hit
    df = pl.read_parquet(f, columns=["week", "game_id"])
    print(f"\nplay-by-play 2026 (pull {day}):")
    for w, games, plays in df.group_by("week").agg(pl.col("game_id").n_unique(), pl.len()).sort("week").iter_rows():
        print(f"  week {w}: {games} games, {plays} plays")


# ---------------------------------------------------------------------------
def freshness(export_dir):
    files = glob.glob(os.path.join(export_dir, "nfl", "players", "*", "2026.json"))
    if not files:
        fail(f"no 2026 player files under {export_dir}")
    gen, last, teams18 = collections.Counter(), {}, collections.Counter()
    for f in files:
        d = json.load(open(f, encoding="utf-8"))
        day = d["generated_at"][:10]
        gen[day] += 1
        reg = [p for p in d["periods"] if p.get("season_type") == "REG"]
        last[d["identity"]["id"]] = max((p["index"] for p in reg), default=0)
        if day == "2026-09-18" and d["periods"]:
            teams18[d["periods"][-1].get("team")] += 1
    print(f"2026 player season files: {len(files)}")
    print("  generated_at by day: " + ", ".join(f"{k} {v}" for k, v in sorted(gen.items())))
    print("  last REG week carried: " + ", ".join(f"wk{k} {v}" for k, v in sorted(collections.Counter(last.values()).items())))
    print("  files stamped 2026-09-18, by team of their last period: "
          + ", ".join(f"{k} {v}" for k, v in teams18.most_common()))
    c = paths.market_log_ro()
    wk2 = {g for (g,) in c.execute("SELECT DISTINCT gsis_id FROM nfl_player_week "
                                   "WHERE season=2026 AND week=2 AND season_type='REG'")}
    c.close()
    if not wk2:
        fail("no week-2 player rows in nfl_player_week")
    have = [g for g in wk2 if g in last]
    lacking = [g for g in have if last[g] < 2]
    print(f"  players with a week-2 stat row {len(wk2)}; with a 2026 file {len(have)}; "
          f"file lacks week 2: {len(lacking)} {lacking[:10]}")
    m = json.load(open(os.path.join(export_dir, "nfl", "manifest.json"), encoding="utf-8"))
    print(f"  manifest generated_at {m['generated_at']}, data_through {m['current'].get('data_through')}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--populations", action="store_true")
    ap.add_argument("--h1-inputs", action="store_true")
    ap.add_argument("--freshness", action="store_true")
    ap.add_argument("--export-dir")
    a = ap.parse_args()
    if not (a.populations or a.h1_inputs or a.freshness):
        ap.error("name at least one part")
    if a.populations:
        populations()
    if a.h1_inputs:
        h1_inputs()
    if a.freshness:
        if not a.export_dir:
            ap.error("--freshness needs --export-dir (read, never written)")
        freshness(a.export_dir)


if __name__ == "__main__":
    main()
