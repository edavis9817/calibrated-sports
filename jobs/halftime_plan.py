"""Brief 021 B1 - where halftime falls, and what capturing it costs.

    python -m jobs.halftime_plan                 # offsets + cost, no network
    python -m jobs.halftime_plan --balance       # + remaining credits (free call)

Offsets are measured, not assumed: for every regular-season game in the
mirrored nflverse play-by-play, minutes from SCHEDULED kickoff (games.parquet
gameday + gametime, US/Eastern) to the last Q2 play and to the first Q3 play.
Scheduled, because that is the only kickoff the Odds API adapter knows.

Cost: one bulk call per poll covers every game in a window, so windows that
overlap across games merge and are paid once. Bulk cost is markets x regions.
`--balance` reads x-requests-remaining from GET /sports, which the Odds API
does not bill.
"""
import argparse
import glob
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402

ET = ZoneInfo("America/New_York")
SEASONS = (2022, 2023, 2024, 2025)


def _latest(pattern):
    files = sorted(glob.glob(os.path.join(config.RAW_DIR, "nflverse", "*", pattern)))
    if not files:
        raise SystemExit(f"no nflverse file matching {pattern} under {config.RAW_DIR}")
    return files[-1]


def schedule():
    import polars as pl
    g = pl.read_parquet(_latest("games.parquet"))
    out = {}
    for gid, season, day, tm in g.select("game_id", "season", "gameday", "gametime").iter_rows():
        if day and tm:
            out[gid] = (season, datetime.strptime(f"{day} {tm}", "%Y-%m-%d %H:%M")
                        .replace(tzinfo=ET).timestamp())
    return out


def offsets(sched, seasons=SEASONS):
    """[(end_q2_min, q3_start_min)] per regular-season game."""
    import polars as pl
    iso = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    out = []
    for season in seasons:
        df = pl.read_parquet(_latest(f"play_by_play_{season}.parquet"),
                             columns=["game_id", "season_type", "qtr", "time_of_day", "play_id"])
        df = df.filter((pl.col("season_type") == "REG") & pl.col("time_of_day").is_not_null())
        for (gid,), g in df.group_by("game_id"):
            if gid not in sched:
                continue
            g = g.sort("play_id")
            q2, q3 = g.filter(pl.col("qtr") == 2), g.filter(pl.col("qtr") == 3)
            if q2.height == 0 or q3.height == 0:
                continue
            k = sched[gid][1]
            a, b = (iso(q2["time_of_day"][-1]) - k) / 60, (iso(q3["time_of_day"][0]) - k) / 60
            if 60 < a < 180 and 0 < b - a < 40:      # drops clock-less / garbled games
                out.append((a, b))
    return out


def q(v, p):
    v = sorted(v)
    return v[int(p * (len(v) - 1))]


def merged_minutes(kicks, lo_min, hi_min):
    iv = sorted((k + lo_min * 60, k + hi_min * 60) for k in kicks)
    merged = []
    for a, b in iv:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return merged, sum((b - a) / 60 for a, b in merged)


def credits_per_call(markets=None, regions=None):
    markets = markets or config.ODDS_HALFTIME_MARKETS
    regions = regions or config.ODDS_REGIONS
    return len(markets.split(",")) * len(regions.split(","))


def cost(kicks, lo_min, hi_min, every_s, per_call):
    windows, minutes = merged_minutes(kicks, lo_min, hi_min)
    calls = minutes * 60 / every_s
    return {"windows": len(windows), "minutes": minutes, "calls": calls,
            "credits": calls * per_call}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--balance", action="store_true")
    ap.add_argument("--from", dest="start", default=None, help="YYYY-MM-DD, default today")
    a = ap.parse_args()
    sched = offsets_src = schedule()
    offs = offsets(offsets_src)
    end_q2, q3 = [x for x, _ in offs], [y for _, y in offs]
    print(f"games measured {len(offs)} (REG {SEASONS[0]}-{SEASONS[-1]})")
    for name, v in (("last Q2 play", end_q2), ("first Q3 play", q3),
                    ("halftime length", [y - x for x, y in offs])):
        print(f"  {name:<16} min after scheduled kickoff: "
              + "  ".join(f"p{int(100 * p)} {q(v, p):.0f}" for p in (.05, .1, .5, .9, .95)))

    start = (datetime.strptime(a.start, "%Y-%m-%d").replace(tzinfo=ET) if a.start
             else datetime.now(ET).replace(hour=0, minute=0, second=0, microsecond=0))
    week_end = start + timedelta(days=7)
    month_end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    kicks = lambda t0, t1: [k for s, k in sched.values() if t0.timestamp() <= k < t1.timestamp()]
    per_call = credits_per_call()
    print(f"\n  cadence {config.ODDS_HALFTIME_EVERY:g}s, markets {config.ODDS_HALFTIME_MARKETS}, "
          f"regions {config.ODDS_REGIONS} -> {per_call} credits/call")
    policies = (("median", q(end_q2, .5) - 10, q(q3, .5) + 10),
                ("p10-p90", q(end_q2, .1) - 10, q(q3, .9) + 10),
                ("p5-p95", q(end_q2, .05) - 10, q(q3, .95) + 10))
    for name, lo, hi in policies:
        cover = sum(1 for x, y in offs if x - 10 >= lo and y + 10 <= hi) / len(offs)
        print(f"\n  [{name}] window kickoff+{lo:.0f}..+{hi:.0f} min, covers whole halftime+-10 "
              f"in {100 * cover:.0f}% of games")
        for label, t0, t1 in (("next 7 days", start, week_end),
                              (f"to end of {start:%B}", start, month_end)):
            ks = kicks(t0, t1)
            c = cost(ks, lo, hi, config.ODDS_HALFTIME_EVERY, per_call)
            print(f"    {label:<18} {len(ks):>3} games  {c['windows']:>3} merged windows  "
                  f"{c['minutes']:>5.0f} min  {c['calls']:>5.0f} calls  {c['credits']:>6.0f} credits")

    if a.balance:
        import httpx
        r = httpx.get(f"{config.ODDS_BASE}/sports", params={"apiKey": config.ODDS_API_KEY},
                      timeout=30)
        rem = int(r.headers.get("x-requests-remaining", -1))
        print(f"\n  balance: remaining {rem:,}  used {r.headers.get('x-requests-used')}  "
              f"this call billed {r.headers.get('x-requests-last')}  "
              f"reserve {config.ODDS_RESERVE:,}  -> spendable {rem - config.ODDS_RESERVE:,}")


if __name__ == "__main__":
    main()
