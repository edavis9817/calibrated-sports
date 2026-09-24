"""f-18: how often does b-25's default start/sit window splice a player's weeks across teams?

b-25's start/sit card distributes a player's scored regular-season weeks over a default window
of THIS SEASON AND LAST (2025 + 2026). This measures, on the served components files, how many
of the players the card lets a reader pick (the 2026 file's QB/RB/WR/TE, as
`components/views/StartSit.tsx` filters them) have rows on more than one team inside that
window - i.e. a distribution built across a team change.

Reads the two served files (fetched over HTTP, or from a directory given with --dir). Nothing
is written anywhere but stdout. Teams are the per-row `team` field, which is the historical team
of that week, the same field the card prints ("teams in the window: LV, JAX").

    python -m research.f18_window_team_changes                # fetch from production
    python -m research.f18_window_team_changes --dir D:/x     # read nfl/components/{2025,2026}.json
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import httpx

BASE = "https://calibratedsports.com/data/nfl/components/{season}.json"
POSITIONS = ("QB", "RB", "WR", "TE")  # config/sports/nfl.ts fantasy.positions on b-25
MIN_WEEKS = 6  # lib/startSit.ts MIN_WEEKS: below this the card prints no quantiles and no verdict
SEASONS = (2025, 2026)


def load(season: int, directory: str | None) -> dict:
    if directory:
        return json.loads((Path(directory) / "nfl" / "components" / f"{season}.json").read_text("utf-8"))
    r = httpx.get(BASE.format(season=season), headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
    r.raise_for_status()
    return r.json()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir")
    args = ap.parse_args()

    files = {s: load(s, args.dir) for s in SEASONS}
    for s, f in files.items():
        assert f["kind"] == "components" and f["season"] == s, (s, f.get("kind"), f.get("season"))
        print(f"{s}: generated_at {f['generated_at']}, {len(f['players'])} players, {len(f['rows'])} rows")

    cur = files[2026]
    pickable = [p for p in cur["players"] if p.get("position") in POSITIONS]
    assert pickable, "no pickable players - the file shape is not what this script expects"

    # player id -> list of (season, week, team) over REG rows in the window
    weeks: dict[str, list[tuple[int, int, str | None]]] = defaultdict(list)
    for s, f in files.items():
        ids = [p["id"] for p in f["players"]]
        for r in f["rows"]:
            if r.get("season_type") != "REG":
                continue
            weeks[ids[r["player"]]].append((s, r["index"], r.get("team")))

    n_all = n_eligible = 0
    multi_all: list[tuple] = []
    multi_eligible: list[tuple] = []
    null_team_rows = 0
    for p in pickable:
        w = weeks.get(p["id"], [])
        null_team_rows += sum(1 for x in w if not x[2])
        teams = []
        for _, _, t in sorted(w):
            if t and t not in teams:
                teams.append(t)
        n_all += 1
        eligible = len(w) >= MIN_WEEKS
        n_eligible += eligible
        if len(teams) > 1:
            by_team = {t: sum(1 for x in w if x[2] == t) for t in teams}
            # where the change sits: between seasons, or inside 2025
            t25 = {x[2] for x in w if x[0] == 2025 and x[2]}
            t26 = {x[2] for x in w if x[0] == 2026 and x[2]}
            where = "inside 2025" if len(t25) > 1 else ("inside 2026" if len(t26) > 1 else "between seasons")
            rec = (p["name"], p["position"], len(w), by_team, where)
            multi_all.append(rec)
            if eligible:
                multi_eligible.append(rec)

    print()
    print(f"pickable players (2026 file, {'/'.join(POSITIONS)}): {n_all}")
    print(f"  with >= {MIN_WEEKS} REG weeks in 2025+2026 (card draws quantiles): {n_eligible}")
    print(f"  on more than one team in the window, all pickable: {len(multi_all)} "
          f"({len(multi_all) / n_all:.1%})")
    print(f"  on more than one team in the window, eligible:     {len(multi_eligible)} "
          f"({len(multi_eligible) / n_eligible:.1%})")
    wc = defaultdict(int)
    for r in multi_eligible:
        wc[r[4]] += 1
    print(f"  eligible, by where the change sits: {dict(wc)}")
    print(f"  null-team REG rows among pickable players: {null_team_rows}")
    print()
    print("eligible players on more than one team (name, pos, weeks, weeks by team, where):")
    for r in sorted(multi_eligible, key=lambda r: (r[1], r[0])):
        print("  ", r)


if __name__ == "__main__":
    main()
