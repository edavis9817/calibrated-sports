"""Unit a-40: can the Teams and Players artboard figures be computed from files
ALREADY PUBLISHED, with no producer field added? Computed here, from the export
tree, the way a page would compute them - so "computable at render" is a
measurement rather than a reading of the schema.

    python -m research.a40_render_inputs                 # WEB_EXPORT_DIR from config
    python -m research.a40_render_inputs --root D:/.../web_export

Reads files only. Writes nothing. Three parts:

1. TEAMS, from `nfl/manifest.json` `teams[]` alone (conference, division,
   season.{games, cleared, missed, tied, points_for, points_against}):
     pythagorean wins  games * PF^2.37 / (PF^2.37 + PA^2.37)
     on pace for       (cleared + tied/2) / games * 17 - arithmetic, not a projection
     division net      sum of (PF - PA) over the division's four teams
   Reconciliations that must hold if the inputs are whole: every team's
   cleared + missed + tied == games; league net points == 0 (every game is in
   both teams' records); 8 divisions of 4.

2. PLAYERS PRICE VIEWS, from `nfl/market/{id}/{season}-{index}.json` for the
   current period and `nfl/players/index.json` `has_market`:
     the rung nearest even money per MARKET component, its mid on the over,
     lean (over / under / even), the 2.5pp histogram, and the fields the price
     axis labels need (team, position, kickoff_ts).
   Checks: p_over equals (bid + ask) / 2 on every rung (the artboard says "mid");
   has_market count equals the number of current-period market files.

3. START OR SIT, from `nfl/components/{season}.json`: weekly PPR points for two
   named players over 2025 and 2026, p10 / median / p90 - with and without the
   snaps > 0 filter, because a zero-snap row is a week he did not play.
"""
import argparse
import glob
import json
import os
import statistics as st
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PYTHAG_EXP = 2.37
SEASON_GAMES = 17
PPR = {"pass_yds": 0.04, "pass_td": 4, "int": -2, "rush_yds": 0.1, "rush_td": 6,
       "rec": 1, "rec_yds": 0.1, "rec_td": 6, "fum_lost": -2, "two_pt": 2, "ret_td": 6}
NAMED = ("Matthew Golden", "Christian Watson")


def load(root, *parts):
    with open(os.path.join(root, *parts), encoding="utf-8") as f:
        return json.load(f)


def teams(root):
    m = load(root, "nfl", "manifest.json")
    rows = m["teams"]
    if len(rows) != 32:
        raise SystemExit("manifest carries %d teams, expected 32" % len(rows))
    out, league_net, bad_record, div = [], 0, [], {}
    for t in rows:
        s = t["season"]
        g, w, l, ti = s["games"], s["cleared"], s["missed"], s["tied"]
        pf, pa = s["points_for"], s["points_against"]
        if w + l + ti != g:
            bad_record.append(t["abbr"])
        pyth = g * pf ** PYTHAG_EXP / (pf ** PYTHAG_EXP + pa ** PYTHAG_EXP) if (pf or pa) else None
        pace = (w + ti / 2) / g * SEASON_GAMES if g else None
        league_net += pf - pa
        div.setdefault(t["division"], []).append((t["abbr"], pf - pa))
        out.append((t["abbr"], t["division"], g, w, l, ti, pf, pa, pyth, pace))
    print("TEAMS - manifest season %s, data through %s"
          % (m["current"]["season"], m["current"]["data_through"]))
    print("  teams 32, divisions %d (sizes %s)" % (len(div), sorted({len(v) for v in div.values()})))
    print("  record reconciles (W+L+T == G) on %d of 32; league net points %+d"
          % (32 - len(bad_record), league_net))
    for abbr, dv, g, w, l, ti, pf, pa, pyth, pace in sorted(out, key=lambda r: r[1])[:4]:
        print("  %-4s %-9s G%d %d-%d-%d  PF %3d PA %3d  pythag %.2f  on pace %.1f"
              % (abbr, dv, g, w, l, ti, pf, pa, pyth, pace))
    for dv, members in sorted(div.items()):
        print("  %-9s net %+4d  teams %s" % (dv, sum(n for _a, n in members),
                                             " ".join("%s%+d" % mm for mm in members)))
    return {"teams": 32, "divisions": len(div), "league_net": league_net,
            "records_reconcile": 32 - len(bad_record)}


def prices(root):
    m = load(root, "nfl", "manifest.json")
    key = "%d-%d" % (m["current"]["season"], m["current"]["period"]["index"])
    files = sorted(glob.glob(os.path.join(root, "nfl", "market", "*", key + ".json")))
    flagged = sum(1 for p in load(root, "nfl", "players", "index.json")["players"]
                  if p.get("has_market"))
    if not files:
        raise SystemExit("no market files for %s - nothing measured is not a result" % key)
    lines, rungs, not_mid, missing = [], 0, 0, 0
    for f in files:
        d = load(root, *os.path.relpath(f, root).split(os.sep))
        ident = d["identity"]
        for need in (ident.get("team"), ident.get("position"), d.get("kickoff_ts")):
            if need is None:
                missing += 1
        for c in d["components"]:
            if c["basis"] != "MARKET" or not c.get("rungs"):
                continue
            for r in c["rungs"]:
                rungs += 1
                if abs(r["p_over"] - (r["bid"] + r["ask"]) / 2) > 1e-9:
                    not_mid += 1
            best = min(c["rungs"], key=lambda r: (abs(r["p_over"] - 0.5), r["line"]))
            lines.append((c["stat"], best["line"], best["p_over"]))
    over = sum(1 for _s, _l, p in lines if p > 0.5)
    under = sum(1 for _s, _l, p in lines if p < 0.5)
    hist = {}
    for _s, _l, p in lines:
        b = int(p * 100 // 2.5)
        hist[b] = hist.get(b, 0) + 1
    print("PRICES - period %s: %d market files, has_market flags %d" % (key, len(files), flagged))
    print("  rungs %d, p_over != (bid+ask)/2 on %d; team/position/kickoff missing %d times"
          % (rungs, not_mid, missing))
    print("  lines shown (rung nearest even, per MARKET component): %d  by stat %s"
          % (len(lines), {s: sum(1 for x in lines if x[0] == s) for s in sorted({x[0] for x in lines})}))
    print("  lean over %d / under %d / even %d; median mid %.3f"
          % (over, under, len(lines) - over - under, st.median(p for *_x, p in lines)))
    print("  2.5pp bands: " + ", ".join("%.1f-%.1f%%:%d" % (b * 2.5, b * 2.5 + 2.5, n)
                                         for b, n in sorted(hist.items())))
    return {"files": len(files), "has_market": flagged, "rungs": rungs,
            "not_mid": not_mid, "lines": len(lines), "missing_fields": missing}


def start_sit(root):
    ids = {p["name"]: p["id"] for p in load(root, "nfl", "players", "index.json")["players"]
           if p["name"] in NAMED}
    weeks = {i: [] for i in ids.values()}
    for season in (2025, 2026):
        d = load(root, "nfl", "components", "%d.json" % season)
        cols = d["columns"]
        missing = [c for c in PPR if c not in cols]
        if missing:
            raise SystemExit("components %d lacks %s" % (season, missing))
        # `player` is a 0-based index into `players` (contract ComponentsRow).
        pid = {i: p["id"] for i, p in enumerate(d["players"])}
        if any(r["player"] not in pid for r in d["rows"]):
            raise SystemExit("a row's player index falls outside players[]")
        for r in d["rows"]:
            gsis = pid.get(r["player"])
            if gsis in weeks:
                v = dict(zip(cols, r["values"]))
                pts = sum(w * (v[c] or 0) for c, w in PPR.items())
                weeks[gsis].append((season, r["index"], r["season_type"], v["snaps"], pts))
    print("START OR SIT - PPR from the components table, 2025 and 2026")
    out = {}
    for name, gsis in ids.items():
        for label, rows in (("all rows", weeks[gsis]),
                            ("snaps > 0", [w for w in weeks[gsis] if w[3]])):
            pts = sorted(w[4] for w in rows)
            if len(pts) < 2:
                print("  %-18s %-9s %d weeks - too few" % (name, label, len(pts)))
                continue
            q = st.quantiles(pts, n=10, method="inclusive")
            print("  %-18s %-9s weeks %2d  p10 %5.1f  median %5.1f  p90 %5.1f"
                  % (name, label, len(pts), q[0], st.median(pts), q[-1]))
            out["%s|%s" % (name, label)] = len(pts)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root")
    a = ap.parse_args(argv)
    root = a.root
    if not root:
        import config
        root = config.WEB_EXPORT_DIR
    if not root or not os.path.isdir(root):
        raise SystemExit("no export tree at %r" % root)
    teams(root)
    prices(root)
    start_sit(root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
