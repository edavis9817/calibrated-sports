"""Brief 023 Part 2 - book versus book: arbitrage and middles, freshness-filtered.

    python -m research.bookvbook [--tests-out PATH]

Pre-registration: docs/briefs/023-preregistration.md (fd0a57b), Part 2. No
forecast anywhere: every number is a comparison of two books' own prices.

POPULATIONS
  hist   raw oddsapi_historical: `featured/*` spreads + totals at every snapshot,
         `event_odds/*` props (receptions, reception yds, rush attempts,
         tackles+assists, sacks) at their single close snapshot. 2023-25 games.
  live   raw oddsapi 2026: `odds_bulk` spreads + totals, `odds_props` props,
         restricted to 2026 WEEK-1 games (week 2 is a brief 022 holdout).
  Pre-kickoff only: snapshot < kickoff - 300s. Kickoff = nflverse gameday +
  gametime, US/Eastern.

FRESHNESS (2a). A quote is usable only if snapshot - last_update <= 600s
(market last_update, falling back to the bookmaker's). A PAIR is comparable
only if the two books differ and |last_update_A - last_update_B| <= 300s.
Curve at 60s / 900s. Every arb that exists in the unfiltered data and not in
the filtered data is counted: that is the size of the stale-quote artifact.

ORIENTATION. Every quote becomes (kind, threshold) on one realized quantity X:
  spreads  X = HOME margin. Home at point p wins iff X > -p  -> ('o', -p)
                            Away at point q wins iff X <  q  -> ('u',  q)
  totals   X = total points. Over L -> ('o', L); Under L -> ('u', L)
  props    X = the player's stat. Over L -> ('o', L); Under L -> ('u', L)
An 'o' at threshold a and a 'u' at threshold b, from different books:
  a == b  ARB candidate:  arb iff implied(o) + implied(u) < 1;
          size = 1 - (implied_o + implied_u), in pp (a balanced-stake return is
          exactly 1/(implied_o + implied_u) - 1; the pre-registered size is the
          first).
  b >  a  MIDDLE, width = b - a; both legs win iff a < X < b.
MIDDLE EV, one unit on EACH leg, reported per unit of total stake:
  EV = sum_x P(x) * [pay_o(x) + pay_u(x)] / 2,
  pay = decimal - 1 on a win, 0 on a push (X exactly on an integer threshold),
  -1 on a loss. The same formula at the realized X gives the realized EV.
P(x) is EMPIRICAL, never normal:
  spreads  nflverse 1999-2025 REG+POST home margins of games whose closing
           spread_line (positive = home favoured) is within +-1 of the pair's
           consensus, the tested game itself excluded
  totals   combined scores of games with total_line within +-1.5, game excluded
  props    realized values (outcome_settlement.actual) of outcome_close
           outcomes with the same stat and the same line as the consensus,
           from seasons OTHER than the tested one (2026: all of 2023-25)
  consensus = the modal threshold across the group's books (ties -> lower).

PERSISTENCE. The same arb/middle (same books, same thresholds) is followed to
the event's later snapshots; survival = time to the last consecutive snapshot
still showing it. A lower bound at the snapshot spacing, which is reported.
"""
import argparse
import glob
import gzip
import json
import os
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from research.sweep.common import boot, mean_of, wilson  # noqa: E402
from venues.mapping import norm_name, team_abbr  # noqa: E402

ET = ZoneInfo("America/New_York")
FETCH_MAX_AGE = 600.0
PAIR_WINDOW = 300.0
CURVE = (60.0, 300.0, 900.0)
PRE_KICK = 300.0
SPREAD_WIN = 1.0
TOTAL_WIN = 1.5
PROP_STAT = {"player_receptions": "receptions", "player_reception_yds": "receiving_yards",
             "player_rush_attempts": "rush_attempts", "player_tackles_assists": "tackles_assists",
             "player_sacks": "sacks"}
CLASSES = ("spreads", "totals") + tuple(PROP_STAT.values())
HIST_SEASONS = (2023, 2024, 2025)


def raw_dir(venue):
    p = os.path.join(config.RAW_DIR, venue)
    return p if os.path.isdir(p) else os.path.join("D:/calibrated-sports/data/raw", venue)


# =============================================================================
# pure pieces
# =============================================================================

def american_to_prob(o):
    o = float(o)
    return 100.0 / (o + 100.0) if o > 0 else -o / (-o + 100.0)


def decimal(o):
    o = float(o)
    return 1.0 + (o / 100.0 if o > 0 else 100.0 / -o)


def outcome(kind, thr, x):
    """+1 win, 0 push, -1 loss for a quote of `kind` at `thr` given realized x."""
    if kind == "o":
        return (x > thr) - (x < thr)
    return (x < thr) - (x > thr)


def pay(price, kind, thr, x):
    r = outcome(kind, thr, x)
    return decimal(price) - 1.0 if r > 0 else (0.0 if r == 0 else -1.0)


def quote_kind(cls, name, point, home_name):
    """(kind, threshold) on the class's X. None if unusable."""
    if point is None:
        return None
    p = float(point)
    if cls == "spreads":
        if name == home_name:
            return ("o", -p)
        return ("u", p)
    n = (name or "").lower()
    if n == "over":
        return ("o", p)
    if n == "under":
        return ("u", p)
    return None


def comparable(a, b, snap, fresh, window):
    """a, b: dicts with book, lu. fresh=None disables the filter entirely."""
    if a["book"] == b["book"]:
        return False
    if fresh is None:
        return True
    for q in (a, b):
        if q["lu"] is None or snap - q["lu"] > fresh:
            return False
    return abs(a["lu"] - b["lu"]) <= window


def mode_threshold(thrs):
    if not thrs:
        return None
    c = Counter(thrs)
    top = max(c.values())
    return min(t for t, n in c.items() if n == top)


def middle_ev(o, u, dist):
    """Per unit of total stake, one unit on each leg. dist: {x: prob}."""
    if not dist:
        return None, None
    ev = sum(p * (pay(o["price"], "o", o["thr"], x) + pay(u["price"], "u", u["thr"], x))
             for x, p in dist.items()) / 2.0
    hit = sum(p for x, p in dist.items() if o["thr"] < x < u["thr"])
    return ev, hit


def analyse_group(quotes, snap, fresh, window, dist=None):
    """One (event, snapshot, class, subject). Returns (arbs, middles):
       arbs    {thr: {"arb": bool, "sum": best, "books": (a, b)}}  (comparable pairs only)
       middles {(a_thr, u_thr): {"ev", "hit", "books", "o", "u"}}  best EV pair"""
    overs = [q for q in quotes if q["kind"] == "o"]
    unders = [q for q in quotes if q["kind"] == "u"]
    arbs, mids = {}, {}
    for o in overs:
        for u in unders:
            if u["thr"] < o["thr"] or not comparable(o, u, snap, fresh, window):
                continue
            if u["thr"] == o["thr"]:
                s = o["imp"] + u["imp"]
                cur = arbs.get(o["thr"])
                if cur is None or s < cur["sum"]:
                    arbs[o["thr"]] = {"arb": s < 1.0, "sum": s, "books": (o["book"], u["book"])}
            else:
                key = (o["thr"], u["thr"])
                ev, hit = middle_ev(o, u, dist) if dist is not None else (None, None)
                cur = mids.get(key)
                score = ev if ev is not None else -(o["imp"] + u["imp"])
                if cur is None or score > cur["_score"]:
                    mids[key] = {"ev": ev, "hit": hit, "books": (o["book"], u["book"]),
                                 "o": o, "u": u, "_score": score}
    return arbs, mids


# =============================================================================
# loading
# =============================================================================

def _iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() if s else None


def load_games():
    import polars as pl
    f = sorted(glob.glob(os.path.join(raw_dir("nflverse"), "*", "games.parquet")))[-1]
    g = pl.read_parquet(f)
    out = {}
    for r in g.iter_rows(named=True):
        if not (r["gameday"] and r["gametime"]):
            continue
        kick = datetime.strptime(f"{r['gameday']} {r['gametime']}", "%Y-%m-%d %H:%M").replace(tzinfo=ET).timestamp()
        out[r["game_id"]] = {"game": r["game_id"], "season": r["season"], "week": r["week"],
                             "type": r["game_type"], "kick": kick, "home": r["home_team"],
                             "away": r["away_team"], "result": r["result"],
                             "points": (None if r["home_score"] is None or r["away_score"] is None
                                        else r["home_score"] + r["away_score"]),
                             "spread_line": r["spread_line"], "total_line": r["total_line"]}
    return out


def game_index(games):
    by = defaultdict(list)
    for g in games.values():
        by[(g["home"], g["away"])].append(g)
    return by


def match_game(ev, idx):
    h, a = team_abbr(ev.get("home_team") or ""), team_abbr(ev.get("away_team") or "")
    ct = _iso(ev.get("commence_time"))
    if not (h and a and ct):
        return None
    cands = [g for g in idx.get((h, a), []) if abs(g["kick"] - ct) < 36 * 3600]
    return min(cands, key=lambda g: abs(g["kick"] - ct)) if cands else None


def iter_snapshots(pop):
    """(snapshot_ts, event_dict) for one population."""
    if pop == "hist":
        for fn in sorted(glob.glob(os.path.join(raw_dir("oddsapi_historical"), "*", "*.jsonl.gz"))):
            with gzip.open(fn, "rt", encoding="utf-8") as f:
                for line in f:
                    r = json.loads(line)
                    ep = r.get("endpoint", "")
                    if not (ep.startswith("featured/") or ep.startswith("event_odds/")):
                        continue
                    p = r.get("payload") or {}
                    snap = _iso(p.get("timestamp"))
                    d = p.get("data")
                    for ev in (d if isinstance(d, list) else [d] if isinstance(d, dict) else []):
                        yield snap, ev
    else:
        for fn in sorted(glob.glob(os.path.join(raw_dir("oddsapi"), "*", "*.jsonl.gz"))):
            with gzip.open(fn, "rt", encoding="utf-8") as f:
                for line in f:
                    r = json.loads(line)
                    ep = r.get("endpoint", "").split(":")[0]
                    if ep not in ("odds_bulk", "odds_props"):
                        continue
                    p = r.get("payload")
                    for ev in (p if isinstance(p, list) else [p] if isinstance(p, dict) else []):
                        yield r["ts"], ev


def extract(pop, games, idx, classes=None):
    """{(pop, game_id, snap, cls, subject): [quote,...]} after the population and
    pre-kickoff fences. Duplicate (book, kind, thr) inside a group keeps one.
    `classes` restricts which market classes are kept."""
    groups = defaultdict(dict)
    census = Counter()
    for snap, ev in iter_snapshots(pop):
        if snap is None or not isinstance(ev, dict) or not ev.get("bookmakers"):
            continue
        g = match_game(ev, idx)
        if g is None:
            census["event not matched to nflverse"] += 1
            continue
        if pop == "hist" and g["season"] not in HIST_SEASONS:
            continue
        if pop == "live" and not (g["season"] == 2026 and g["week"] == 1 and g["type"] == "REG"):
            continue
        if snap >= g["kick"] - PRE_KICK:
            census["event-snapshot not pre-kickoff"] += 1
            continue
        home_name = ev.get("home_team")
        for b in ev["bookmakers"]:
            for m in b.get("markets") or []:
                key = m.get("key")
                cls = key if key in ("spreads", "totals") else PROP_STAT.get(key)
                if cls is None or (classes is not None and cls not in classes):
                    continue
                lu = _iso(m.get("last_update") or b.get("last_update"))
                for oc in m.get("outcomes") or []:
                    if oc.get("price") is None:
                        continue
                    kt = quote_kind(cls, oc.get("name"), oc.get("point"), home_name)
                    if kt is None:
                        continue
                    subject = "" if cls in ("spreads", "totals") else norm_name(oc.get("description") or "")
                    if cls not in ("spreads", "totals") and not subject:
                        continue
                    gk = (pop, g["game"], snap, cls, subject)
                    q = {"book": b.get("key"), "kind": kt[0], "thr": kt[1], "price": oc["price"],
                         "imp": american_to_prob(oc["price"]), "lu": lu,
                         "name": oc.get("description")}
                    groups[gk][(q["book"], q["kind"], q["thr"])] = q
                    census["quotes"] += 1
    return {k: list(v.values()) for k, v in groups.items()}, census


# =============================================================================
# empirical distributions and realized values
# =============================================================================

SETTLEMENTS = ("pre-registered", "corrected")


def snap_status(con):
    """(gsis_id, season, week) -> (team, offense_snaps) from nfl_snap_counts at
    its newest data_version, pfr_player_id mapped to gsis via player_xwalk.

    Why (coordinator's correction, added after the pre-registration): nflverse
    weekly player stats carry NO row for a player who played and recorded no
    stat. A missing row with offensive snaps > 0 is a realized 0; a missing row
    with no snap row or 0 offensive snaps is a player who did not play - void.
    """
    pfr = {p: g for g, p in con.execute("SELECT gsis_id, pfr_id FROM player_xwalk WHERE pfr_id IS NOT NULL")}
    out = {}
    for pid, season, week, team, osn, dsn, _dv in con.execute(
            "SELECT pfr_player_id, season, week, team, offense_snaps, defense_snaps, data_version "
            "FROM nfl_snap_counts WHERE season >= 2023 ORDER BY data_version"):
        g = pfr.get(pid)
        if g:
            out[(g, season, week)] = (team, osn or 0, dsn or 0)
    return out


# Amendment (coordinator): defensive props settle on DEFENSIVE snaps. A defender
# who played and recorded nothing has no stats row either; judging him on
# offensive snaps (always 0) would void exactly his zero outcomes.
DEFENSIVE_STATS = {"tackles_assists", "sacks"}


def corrected_value(pw_value, has_pw_row, snap, stat=None):
    """The corrected settlement rule. Returns (value, status).
    snap = (team, offense_snaps, defense_snaps) or None."""
    if has_pw_row:
        return pw_value, ("value" if pw_value is not None else "null stat")
    played = None if snap is None else (snap[2] if stat in DEFENSIVE_STATS else snap[1])
    if (played or 0) > 0:
        return 0.0, "played, no row -> 0"
    return None, "did not play -> void"


class Empirical:
    def __init__(self, games, con, settlement="pre-registered", snaps=None):
        self.hist_games = [g for g in games.values()
                           if g["season"] <= 2025 and g["type"] in ("REG", "WC", "DIV", "CON", "SB", "POST")
                           and g["result"] is not None]
        xs = [g["spread_line"] for g in self.hist_games if g["spread_line"] is not None]
        ys = [g["result"] for g in self.hist_games if g["spread_line"] is not None]
        mx, my = statistics.fmean(xs), statistics.fmean(ys)
        self.sign_corr = (sum((x - mx) * (y - my) for x, y in zip(xs, ys))
                          / (sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)) ** 0.5)
        self._props = defaultdict(Counter)       # (season, stat, line) -> Counter(actual)
        self.settlement = settlement
        self.census = Counter()
        seen = set()
        for season, week, ent, stat, line, actual in con.execute(
                "SELECT o.season, o.week, o.entity_id, o.stat, o.line, s.actual "
                "FROM outcome_close oc JOIN outcomes o USING(outcome_id) "
                "LEFT JOIN outcome_settlement s USING(outcome_id) "
                "WHERE o.entity_type='player' AND o.stat IS NOT NULL"):
            k = (season, week, ent, stat, line)
            if k in seen:
                continue
            seen.add(k)
            if actual is None:
                if settlement == "pre-registered":
                    self.census["no actual -> excluded"] += 1
                    continue
                v, status = corrected_value(None, False, (snaps or {}).get((ent, season, week)), stat)
                self.census[status] += 1
                if v is None:
                    continue
                actual = v
            self._props[(season, stat, float(line))][float(actual)] += 1
        self._cache = {}

    @staticmethod
    def _norm(c):
        n = sum(c.values())
        return {x: v / n for x, v in c.items()} if n else None

    def spreads(self, s_cons, game_id):
        key = ("s", s_cons, game_id)
        if key not in self._cache:
            c = Counter(g["result"] for g in self.hist_games
                        if g["spread_line"] is not None and abs(g["spread_line"] - s_cons) <= SPREAD_WIN
                        and g["game"] != game_id)
            self._cache[key] = self._norm(c)
        return self._cache[key]

    def totals(self, t_cons, game_id):
        key = ("t", t_cons, game_id)
        if key not in self._cache:
            c = Counter(g["points"] for g in self.hist_games
                        if g["total_line"] is not None and g["points"] is not None
                        and abs(g["total_line"] - t_cons) <= TOTAL_WIN and g["game"] != game_id)
            self._cache[key] = self._norm(c)
        return self._cache[key]

    def props(self, stat, line, season):
        key = ("p", stat, line, season)
        if key not in self._cache:
            c = Counter()
            for s in HIST_SEASONS:
                if s != season:
                    c.update(self._props.get((s, stat, float(line)), Counter()))
            self._cache[key] = self._norm(c)
        return self._cache[key]


class Realized:
    """Player-stat actuals, name -> gsis resolved in memory (alias exact, then
    narrowed to the two teams in the game). Unresolved -> None, counted."""

    def __init__(self, con, settlement="pre-registered", snaps=None):
        self.settlement = settlement
        self.snaps = snaps or {}
        self.alias = defaultdict(set)
        for a, gid in con.execute("SELECT alias, gsis_id FROM player_alias"):
            self.alias[a].add(gid)
        self.pw = {}
        for gid, season, week, team, rec, yds, car, solo, wa, ast, sacks, dv in con.execute(
                "SELECT gsis_id, season, week, team, receptions, receiving_yards, carries, "
                "def_tackles_solo, def_tackles_with_assist, def_tackle_assists, def_sacks, data_version "
                "FROM nfl_player_week WHERE season >= 2023 ORDER BY data_version"):
            self.pw[(gid, season, week)] = {
                "team": team, "receptions": rec, "receiving_yards": yds, "rush_attempts": car,
                "tackles_assists": (None if solo is None else (solo or 0) + (wa or 0) + (ast or 0)),
                "sacks": sacks}
        self.census = Counter()

    def value(self, name_norm, stat, game):
        if self.settlement == "corrected":
            return self._value_corrected(name_norm, stat, game)
        ids = self.alias.get(name_norm, set())
        rows = [(gid, self.pw.get((gid, game["season"], game["week"]))) for gid in ids]
        rows = [(gid, r) for gid, r in rows if r and r["team"] in (game["home"], game["away"])]
        if len({gid for gid, _ in rows}) != 1:
            self.census["unresolved" if not rows else "ambiguous"] += 1
            return None
        v = rows[0][1].get(stat)
        return None if v is None else float(v)

    def _value_corrected(self, name_norm, stat, game):
        teams = (game["home"], game["away"])
        hits = {}
        for gid in self.alias.get(name_norm, set()):
            pw = self.pw.get((gid, game["season"], game["week"]))
            snap = self.snaps.get((gid, game["season"], game["week"]))
            if pw and pw["team"] in teams:
                hits[gid] = corrected_value(pw.get(stat), True, snap, stat)
            elif not pw and snap and snap[0] in teams:
                hits[gid] = corrected_value(None, False, snap, stat)
        if len(hits) != 1:
            self.census["unresolved" if not hits else "ambiguous"] += 1
            return None
        v, status = next(iter(hits.values()))
        self.census[status] += 1
        return None if v is None else float(v)


# =============================================================================
# analysis
# =============================================================================

def run(pops=("hist", "live"), tests_out=None):
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    games = load_games()
    idx = game_index(games)
    emp = Empirical(games, con)
    real = Realized(con)
    print("=" * 78)
    print("BRIEF 023 PART 2 - BOOK VERSUS BOOK (freshness-filtered, pre-kickoff)")
    print("=" * 78)
    print(f"  spread_line sign check: corr(home margin, spread_line) = {emp.sign_corr:+.3f} "
          "(positive = home favoured, as assumed)")
    intervals = []
    all_arbs, all_mids = [], []
    spacing = defaultdict(list)
    curve_rows = []
    for pop in pops:
        groups, census = extract(pop, games, idx)
        print(f"\n[{pop}] quotes {census['quotes']:,}; groups (event x snapshot x class x subject) "
              f"{len(groups):,}; unmatched events {census['event not matched to nflverse']}; "
              f"event-snapshots dropped as not pre-kickoff {census['event-snapshot not pre-kickoff']}")
        # freshness census on quotes
        qn = stale = nolu = 0
        for (p_, gid, snap, cls, subj), qs in groups.items():
            for q in qs:
                qn += 1
                if q["lu"] is None:
                    nolu += 1
                elif snap - q["lu"] > FETCH_MAX_AGE:
                    stale += 1
        print(f"  quotes older than {FETCH_MAX_AGE:.0f}s at the snapshot: {stale:,} of {qn:,} "
              f"({100 * stale / max(qn, 1):.1f}%); no last_update: {nolu}")
        # snapshot sequences for persistence and spacing
        seq = defaultdict(list)
        for (p_, gid, snap, cls, subj) in groups:
            seq[(gid, cls, subj)].append(snap)
        for k in seq:
            seq[k].sort()
            cls = k[1]
            spacing[(pop, cls)] += [b - a for a, b in zip(seq[k], seq[k][1:])]

        for window_label, fresh, window in (("unfiltered", None, None),) + tuple(
                (f"pair<={int(w)}s", FETCH_MAX_AGE, w) for w in CURVE):
            primary = window == PAIR_WINDOW
            n_pairs_opps = Counter()
            arbs_rows, mid_rows = [], []
            present = defaultdict(set)   # (gid, cls, subj, kind, thr..., books) -> snaps
            for (p_, gid, snap, cls, subj), qs in groups.items():
                g = games[gid]
                dist = None
                if primary:
                    othr = [q["thr"] for q in qs if q["kind"] == "o"]
                    cons = mode_threshold(othr)
                    if cons is not None:
                        if cls == "spreads":
                            # the 'o' threshold IS the home-favoured spread_line
                            # convention: home -3.5 -> threshold 3.5 -> spread_line 3.5
                            dist = emp.spreads(cons, gid)
                        elif cls == "totals":
                            dist = emp.totals(cons, gid)
                        else:
                            dist = emp.props(cls, cons, g["season"] if pop == "hist" else 2026)
                arbs, mids = analyse_group(qs, snap, fresh, window, dist)
                for thr, a in arbs.items():
                    arbs_rows.append({"pop": pop, "cls": cls, "game": gid, "snap": snap, "subj": subj,
                                      "thr": thr, "arb": a["arb"], "size": 1.0 - a["sum"],
                                      "books": a["books"]})
                    if a["arb"]:
                        present[("arb", gid, cls, subj, thr, a["books"])].add(snap)
                if primary:
                    for (ot, ut), m in mids.items():
                        x = None
                        if cls == "spreads":
                            x = g["result"]
                        elif cls == "totals":
                            x = g["points"]
                        else:
                            x = real.value(subj, cls, g)
                        ev_r = None if x is None else (pay(m["o"]["price"], "o", ot, x) + pay(m["u"]["price"], "u", ut, x)) / 2
                        mid_rows.append({"pop": pop, "cls": cls, "game": gid, "snap": snap, "subj": subj,
                                         "width": ut - ot, "ev": m["ev"], "hit": m["hit"],
                                         "hit_real": None if x is None else float(ot < x < ut),
                                         "ev_real": ev_r, "books": m["books"], "thr": (ot, ut)})
                        present[("mid", gid, cls, subj, (ot, ut), m["books"])].add(snap)
            n_arb = sum(r["arb"] for r in arbs_rows)
            curve_rows.append((pop, window_label, len(arbs_rows), n_arb))
            if window_label == "unfiltered":
                unf = {(r["cls"], r["game"], r["snap"], r["subj"], r["thr"]) for r in arbs_rows if r["arb"]}
                continue
            if not primary:
                continue
            filt = {(r["cls"], r["game"], r["snap"], r["subj"], r["thr"]) for r in arbs_rows if r["arb"]}
            print(f"\n  STALE-QUOTE ARTIFACT [{pop}]: unfiltered arbs {len(unf):,}; still arbs under the "
                  f"filter {len(unf & filt):,}; vanished {len(unf - filt):,} "
                  f"({100 * len(unf - filt) / max(len(unf), 1):.1f}%)")
            # persistence
            def survival(kind):
                out = []
                for key, snaps in present.items():
                    if key[0] != kind:
                        continue
                    s_all = seq[(key[1], key[2], key[3])]
                    order = sorted(snaps)
                    for t in order:
                        i = s_all.index(t)
                        if i > 0 and s_all[i - 1] in snaps:
                            continue
                        j = i
                        while j + 1 < len(s_all) and s_all[j + 1] in snaps:
                            j += 1
                        out.append({"cls": key[2], "surv": s_all[j] - t,
                                    "next": j > i, "had_next": i + 1 < len(s_all)})
                return out
            arb_surv, mid_surv = survival("arb"), survival("mid")
            all_arbs += [dict(r, surv=None) for r in arbs_rows]
            all_mids += mid_rows
            report_pop(pop, arbs_rows, mid_rows, arb_surv, mid_surv, spacing, intervals)
    print("\nFRESHNESS CURVE (a curve, not a result): pair window -> comparable arb opportunities / arbs")
    for pop, lab, n, a in curve_rows:
        print(f"  [{pop}] {lab:<14} opportunities {n:>9,}  arbs {a:>7,}  rate {100 * a / max(n, 1):.3f}%")
    print(f"\n  realized prop values: {dict(real.census)} unresolved/ambiguous lookups (excluded from realized columns)")
    est = [r for r in intervals if r["res"]]
    print(f"\nTEST COUNT: {len(intervals)} intervals registered, {len(est)} estimable")
    if tests_out:
        with open(tests_out, "w", encoding="utf-8") as f:
            for r in intervals:
                f.write(json.dumps({k: v for k, v in r.items()}) + "\n")
    return intervals


def report_pop(pop, arbs_rows, mid_rows, arb_surv, mid_surv, spacing, intervals):
    print(f"\n  ARBITRAGE [{pop}] (primary: fresh <= {FETCH_MAX_AGE:.0f}s, pair <= {PAIR_WINDOW:.0f}s)")
    print(f"    {'class':<16}{'opps':>9}{'arbs':>7}{'rate [Wilson]':>24}{'size med / p90 / max pp':>28}"
          f"{'games':>7}{'survive next':>14}{'surv med min':>14}{'spacing med min':>17}")
    for cls in CLASSES:
        rows = [r for r in arbs_rows if r["cls"] == cls]
        if not rows:
            continue
        arbs = [r for r in rows if r["arb"]]
        lo, hi = wilson(len(arbs), len(rows))
        sizes = sorted(100 * r["size"] for r in arbs)
        sv = [s for s in arb_surv if s["cls"] == cls and s["had_next"]]
        sp = spacing.get((pop, cls), [])
        print(f"    {cls:<16}{len(rows):>9,}{len(arbs):>7}"
              f"{100 * len(arbs) / len(rows):>8.3f}% [{100 * lo:.3f},{100 * hi:.3f}]"
              + (f"{sizes[len(sizes) // 2]:>10.2f} / {sizes[int(.9 * (len(sizes) - 1))]:.2f} / {sizes[-1]:.2f}"
                 if sizes else f"{'-':>24}")
              + f"{len({r['game'] for r in arbs}):>7}"
              + (f"{100 * sum(s['next'] for s in sv) / len(sv):>13.0f}%" if sv else f"{'-':>14}")
              + (f"{statistics.median(s['surv'] for s in sv) / 60:>14.1f}" if sv else f"{'-':>14}")
              + (f"{statistics.median(sp) / 60:>17.1f}" if sp else f"{'n/a (1 snap)':>17}"))
        rate_rows = [{"game": r["game"], "v": 1.0 if r["arb"] else 0.0} for r in rows]
        intervals.append({"name": f"{pop}|{cls}|arb rate", "res": boot(rate_rows, mean_of("v"))})
        intervals.append({"name": f"{pop}|{cls}|mean arb size",
                          "res": boot([{"game": r["game"], "v": r["size"]} for r in arbs], mean_of("v"))})
        top = Counter(tuple(sorted(r["books"])) for r in arbs).most_common(3)
        if top:
            print(f"      top book pairs: " + "; ".join(f"{a}+{b} {n}" for (a, b), n in top))
    print(f"\n  MIDDLES [{pop}] (best-EV fresh pair per (event, snapshot, subject, thresholds))")
    print(f"    {'class':<16}{'middles':>9}{'width med / max':>18}{'P(hit) emp':>12}{'realized [Wilson]':>26}"
          f"{'EV emp pp':>11}{'EV real pp':>12}{'EV>0 share':>12}{'survive next':>14}{'surv med min':>14}")
    for cls in CLASSES:
        rows = [r for r in mid_rows if r["cls"] == cls]
        if not rows:
            continue
        widths = sorted(r["width"] for r in rows)
        emp_rows = [r for r in rows if r["ev"] is not None]
        real_rows = [r for r in rows if r["hit_real"] is not None]
        k = sum(r["hit_real"] for r in real_rows)
        lo, hi = wilson(int(k), len(real_rows)) if real_rows else (float("nan"), float("nan"))
        sv = [s for s in mid_surv if s["cls"] == cls and s["had_next"]]
        print(f"    {cls:<16}{len(rows):>9,}{widths[len(widths) // 2]:>10.1f} / {widths[-1]:<5.1f}"
              + (f"{100 * statistics.fmean(r['hit'] for r in emp_rows):>11.2f}%" if emp_rows else f"{'-':>12}")
              + (f"{100 * k / len(real_rows):>10.2f}% [{100 * lo:.2f},{100 * hi:.2f}]" if real_rows else f"{'-':>26}")
              + (f"{100 * statistics.fmean(r['ev'] for r in emp_rows):>+11.2f}" if emp_rows else f"{'-':>11}")
              + (f"{100 * statistics.fmean(r['ev_real'] for r in real_rows):>+12.2f}" if real_rows else f"{'-':>12}")
              + (f"{100 * sum(r['ev'] > 0 for r in emp_rows) / len(emp_rows):>11.1f}%" if emp_rows else f"{'-':>12}")
              + (f"{100 * sum(s['next'] for s in sv) / len(sv):>13.0f}%" if sv else f"{'-':>14}")
              + (f"{statistics.median(s['surv'] for s in sv) / 60:>14.1f}" if sv else f"{'-':>14}"))
        intervals.append({"name": f"{pop}|{cls}|middle mean EV (empirical)",
                          "res": boot([{"game": r["game"], "v": r["ev"]} for r in emp_rows], mean_of("v"))})
        intervals.append({"name": f"{pop}|{cls}|middle mean EV (realized)",
                          "res": boot([{"game": r["game"], "v": r["ev_real"]} for r in real_rows], mean_of("v"))})
    print(f"\n  INTERVALS [{pop}] (game block bootstrap; pp)")
    for r in intervals:
        if not r["name"].startswith(pop + "|"):
            continue
        res = r["res"]
        if not res:
            print(f"    {r['name']:<48} n/a")
            continue
        scale = 100.0
        star = "*" if res["lo"] > 0 or res["hi"] < 0 else " "
        print(f"    {r['name']:<48} {scale * res['est']:+8.3f} [{scale * res['lo']:+8.3f}, {scale * res['hi']:+8.3f}]{star}"
              f" p={res['p']:.1e} n={res['n']} games={res['games']}")


def run_props_settlement(pops=("hist", "live"), tests_out=None):
    """Props middles only, under BOTH settlement handlings, side by side.

    Added after the main run at the coordinator's instruction: nflverse weekly
    stats omit a player who played and recorded no stat, so the pre-registered
    handling (missing row = excluded) drops exactly the zero outcomes. Only
    middles use realized values; arbitrage does not, so it is not re-run.
    """
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    games = load_games()
    idx = game_index(games)
    snaps = snap_status(con)
    arms = {s: (Empirical(games, con, s, snaps), Realized(con, s, snaps)) for s in SETTLEMENTS}
    props = tuple(PROP_STAT.values())
    intervals = []
    print("=" * 78)
    print("BRIEF 023 PART 2 - PROPS MIDDLES UNDER BOTH SETTLEMENT HANDLINGS")
    print("=" * 78)
    print("  pre-registered: missing nfl_player_week row -> excluded")
    print("  corrected:      missing row AND snaps > 0 -> realized 0; missing row AND (no snap row OR 0 snaps) -> void")
    print("                  snaps = OFFENSIVE for receptions / reception yds / rush attempts,")
    print("                          DEFENSIVE for tackles+assists / sacks (coordinator amendment)")
    for s, (emp, _r) in arms.items():
        print(f"  empirical-distribution census [{s}]: {dict(emp.census)}")
    for pop in pops:
        groups, _c = extract(pop, games, idx, classes=props)
        rows = {s: [] for s in SETTLEMENTS}
        for (p_, gid, snap, cls, subj), qs in groups.items():
            g = games[gid]
            cons = mode_threshold([q["thr"] for q in qs if q["kind"] == "o"])
            if cons is None:
                continue
            season = g["season"] if pop == "hist" else 2026
            for s, (emp, real) in arms.items():
                dist = emp.props(cls, cons, season)
                _a, mids = analyse_group(qs, snap, FETCH_MAX_AGE, PAIR_WINDOW, dist)
                for (ot, ut), m in mids.items():
                    x = real.value(subj, cls, g)
                    ev_r = None if x is None else (pay(m["o"]["price"], "o", ot, x) + pay(m["u"]["price"], "u", ut, x)) / 2
                    rows[s].append({"cls": cls, "game": gid, "ev": m["ev"], "hit": m["hit"], "low": ot <= 1.5,
                                    "hit_real": None if x is None else float(ot < x < ut), "ev_real": ev_r})
        print(f"\n  [{pop}]")
        print(f"    {'class':<16}{'handling':<16}{'middles':>8}{'P(hit) emp':>11}{'realized [Wilson]':>26}"
              f"{'n real':>8}{'EV emp pp':>11}{'EV real pp':>12}{'low-line (<=1.5) emp / real hit':>34}")
        for cls in props:
            for s in SETTLEMENTS:
                rs = [r for r in rows[s] if r["cls"] == cls]
                if not rs:
                    continue
                er = [r for r in rs if r["ev"] is not None]
                rr = [r for r in rs if r["hit_real"] is not None]
                k = sum(r["hit_real"] for r in rr)
                lo, hi = wilson(int(k), len(rr)) if rr else (float("nan"), float("nan"))
                le = [r for r in er if r["low"]]
                lr = [r for r in rr if r["low"]]
                print(f"    {cls:<16}{s:<16}{len(rs):>8,}"
                      + (f"{100 * statistics.fmean(r['hit'] for r in er):>10.2f}%" if er else f"{'-':>11}")
                      + (f"{100 * k / len(rr):>10.2f}% [{100 * lo:.2f},{100 * hi:.2f}]" if rr else f"{'-':>26}")
                      + f"{len(rr):>8}"
                      + (f"{100 * statistics.fmean(r['ev'] for r in er):>+11.2f}" if er else f"{'-':>11}")
                      + (f"{100 * statistics.fmean(r['ev_real'] for r in rr):>+12.2f}" if rr else f"{'-':>12}")
                      + (f"{100 * statistics.fmean(r['hit'] for r in le):>16.2f}% / "
                         + (f"{100 * statistics.fmean(r['hit_real'] for r in lr):.2f}% (n={len(lr)})" if lr else "-")
                         if le else f"{'-':>34}"))
                if s == "corrected":
                    intervals.append({"name": f"{pop}|{cls}|middle mean EV (empirical) [corrected settlement]",
                                      "res": boot([{"game": r["game"], "v": r["ev"]} for r in er], mean_of("v"))})
                    intervals.append({"name": f"{pop}|{cls}|middle mean EV (realized) [corrected settlement]",
                                      "res": boot([{"game": r["game"], "v": r["ev_real"]} for r in rr], mean_of("v"))})
    for s, (_e, real) in arms.items():
        print(f"\n  realized lookups [{s}]: {dict(real.census)}")
    print("\n  INTERVALS [corrected settlement] (game block bootstrap; pp)")
    for r in intervals:
        res = r["res"]
        if not res:
            print(f"    {r['name']:<72} n/a")
            continue
        star = "*" if res["lo"] > 0 or res["hi"] < 0 else " "
        print(f"    {r['name']:<72} {100 * res['est']:+8.3f} [{100 * res['lo']:+8.3f}, {100 * res['hi']:+8.3f}]{star}"
              f" p={res['p']:.1e} n={res['n']} games={res['games']}")
    print(f"\n  ADDED TESTS: {len(intervals)} intervals, {sum(1 for r in intervals if r['res'])} estimable")
    if tests_out:
        with open(tests_out, "a", encoding="utf-8") as f:
            for r in intervals:
                f.write(json.dumps(r) + "\n")
    return intervals


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tests-out")
    ap.add_argument("--pops", default="hist,live")
    ap.add_argument("--props-settlement", action="store_true",
                    help="props middles only, pre-registered vs corrected settlement")
    a = ap.parse_args()
    if a.props_settlement:
        run_props_settlement(tuple(a.pops.split(",")), a.tests_out)
    else:
        run(tuple(a.pops.split(",")), a.tests_out)


if __name__ == "__main__":
    main()
