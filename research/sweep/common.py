"""Brief 022 shared rules: the fences, the registry, the statistics.

Every sweep module imports its fences and statistics from here, so the rules in
docs/briefs/022-preregistration.md live in exactly one place.
"""
import json
import math
import os
import random
import re
import sqlite3
import statistics
import subprocess
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

import config

ET = ZoneInfo("America/New_York")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WEEK1_DATES = ("26SEP09", "26SEP10", "26SEP13", "26SEP14")
WEEK2_DATES = ("26SEP17", "26SEP18", "26SEP19", "26SEP20", "26SEP21", "26SEP22")
# After week-1 MNF, before any week-2 game. Fences UNDATED series only.
UNDATED_CUTOFF_TS = datetime(2026, 9, 15, 2, 0, tzinfo=ET).timestamp()
CANDIDATES_DOC = "docs/briefs/022-candidates.md"

# Which population a run measures. Default is the search set. `nfl_wk2` is
# holdout A: selecting it swaps the fences so the SAME module code measures
# week 2, and it refuses (see `open_population`) until the candidates are
# committed and week-2 MNF has a final score. Read at import, so set it in the
# environment before the module is imported.
POPULATION = os.getenv("SWEEP_POPULATION", "nfl_wk1")
if POPULATION not in ("nfl_wk1", "nfl_wk2"):
    raise ValueError(f"SWEEP_POPULATION must be nfl_wk1 or nfl_wk2, got {POPULATION!r}")
WEEK2_UNDATED_FROM_TS = UNDATED_CUTOFF_TS
WEEK2_UNDATED_CUTOFF_TS = datetime(2026, 9, 22, 2, 0, tzinfo=ET).timestamp()
SEASON, WEEK = 2026, (1 if POPULATION == "nfl_wk1" else 2)
ROLE = "search" if POPULATION == "nfl_wk1" else "replication"
CFB_DB = os.path.join(os.path.dirname(os.path.abspath(config.DB_PATH)), "cfb_probe.db")

_DATE = re.compile(r"-(\d{2}[A-Z]{3}\d{2})")

BOOT = 2000
SEED = 22
MIN_GAMES_TO_READ = 5


class HoldoutViolation(RuntimeError):
    """Something tried to read a population that is not open yet."""


# =============================================================================
# fences
# =============================================================================

def ticker_date(market_or_event_id):
    m = _DATE.search(market_or_event_id or "")
    return m.group(1) if m else None


def population_dates():
    return WEEK1_DATES if POPULATION == "nfl_wk1" else WEEK2_DATES


def undated_window():
    if POPULATION == "nfl_wk1":
        return (float("-inf"), UNDATED_CUTOFF_TS)
    return (WEEK2_UNDATED_FROM_TS, WEEK2_UNDATED_CUTOFF_TS)


def in_search_set(market_id, ts):
    """The population being measured (week 1 unless SWEEP_POPULATION says
    otherwise): dated tickers by date at any ts, undated series by window."""
    d = ticker_date(market_id)
    if d is None:
        lo, hi = undated_window()
        return lo <= ts < hi
    return d in population_dates()


def assert_search_set(market_id, ts):
    """In the search set, a week-2 row is a holdout violation. In the week-2
    population run the holdout has been opened on purpose, so nothing raises."""
    if POPULATION != "nfl_wk1":
        return
    d = ticker_date(market_id)
    if d in WEEK2_DATES or (d is None and ts >= UNDATED_CUTOFF_TS):
        raise HoldoutViolation(f"week-2 holdout row: {market_id} @ {ts}")


def week1_filter_sql(col="market_id"):
    """SQL predicate selecting the population's dated tickers (the name is
    historical: in a week-2 run it selects week 2)."""
    dates = population_dates()
    ors = " OR ".join(f"{col} LIKE ?" for _ in dates)
    return f"({ors})", [f"%-{d}%" for d in dates]


def registry_path(name):
    """results/<name>.jsonl for week 1, results/<name>_wk2.jsonl for week 2, so a
    replication run can never overwrite the search registries."""
    suffix = "" if POPULATION == "nfl_wk1" else "_wk2"
    return os.path.join(ROOT, "research", "sweep", "results", f"{name}{suffix}.jsonl")


def week2_settled(con=None):
    """Every week-2 game has a final score in nfl_games (latest data_version)."""
    con = con or live_ro()
    rows = con.execute(
        "SELECT g.game_id, g.home_score FROM nfl_games g WHERE season=? AND week=2 "
        "AND data_version=(SELECT MAX(data_version) FROM nfl_games h WHERE h.game_id=g.game_id)",
        (SEASON,)).fetchall()
    return bool(rows) and all(s is not None for _, s in rows)


def open_population(require=None, settled=None):
    """Holdout A opens only after the candidates are committed AND week 2 has
    settled. No-op for the search set."""
    if POPULATION == "nfl_wk1":
        return
    (require or require_committed)(CANDIDATES_DOC)
    if not (settled or week2_settled)():
        raise HoldoutViolation("week 2 has not settled (a week-2 game has no final score)")


def _git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def require_committed(path=CANDIDATES_DOC):
    """The holdout opens only after the candidates and their mechanisms are in
    HEAD. Machine-checked, not remembered."""
    r = _git("log", "-1", "--format=%H %cI", "--", path)
    if r.returncode != 0 or not r.stdout.strip():
        raise HoldoutViolation(f"{path} is not committed - the holdout is closed")
    dirty = _git("status", "--porcelain", "--", path).stdout.strip()
    if dirty:
        raise HoldoutViolation(f"{path} has uncommitted changes - commit before opening the holdout")
    return r.stdout.split()[0]


def live_ro():
    return sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)


def cfb_ro():
    """CFB is holdout B. ALL CFB access - population runs included - waits for
    the candidates doc, because a population run of H2 before the candidates
    are frozen would be an unlabelled look at H2's own replication."""
    require_committed(CANDIDATES_DOC)
    return sqlite3.connect(f"file:{CFB_DB}?mode=ro", uri=True)


def cfb_raw_dir():
    require_committed(CANDIDATES_DOC)
    return os.path.join(config.RAW_DIR, "cfb_kalshi")


# =============================================================================
# statistics
# =============================================================================

def wilson(k, n, z=1.96):
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def norm_p(z):
    return math.erfc(abs(z) / math.sqrt(2))


def boot(rows, stat, block="game", n=BOOT, seed=SEED):
    """Game block bootstrap of any statistic. Returns est, lo, hi, se, p, n, games."""
    by = defaultdict(list)
    for r in rows:
        by[r[block]].append(r)
    keys = list(by)
    est = stat(rows)
    if est is None or len(keys) < 2:
        return None
    rng = random.Random(seed)
    draws = []
    for _ in range(n):
        sample = [r for _ in keys for r in by[keys[rng.randrange(len(keys))]]]
        v = stat(sample)
        if v is not None and not (isinstance(v, float) and math.isnan(v)):
            draws.append(v)
    if len(draws) < 20:
        return None
    draws.sort()
    se = statistics.pstdev(draws)
    # A zero-variance bootstrap is a statistic that cannot move under
    # resampling (every value identical, or one block) - no evidence either
    # way, so p = 1. It used to be 0, which put a 5-market constant into BH as
    # the most significant test in the sweep.
    p = norm_p(est / se) if se > 0 else 1.0
    return {"est": est, "lo": draws[int(0.025 * len(draws))],
            "hi": draws[int(0.975 * len(draws)) - 1], "se": se, "p": p,
            "n": len(rows), "games": len(keys)}


def mean_of(field):
    def f(rows):
        v = [r[field] for r in rows if r.get(field) is not None]
        return statistics.fmean(v) if v else None
    return f


def slope_of(xf, yf):
    def f(rows):
        xs = [r[xf] for r in rows if r.get(xf) is not None and r.get(yf) is not None]
        ys = [r[yf] for r in rows if r.get(xf) is not None and r.get(yf) is not None]
        if len(xs) < 3:
            return None
        mx, my = statistics.fmean(xs), statistics.fmean(ys)
        vx = sum((x - mx) ** 2 for x in xs)
        return None if vx == 0 else sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / vx
    return f


def bh(pvals, q=0.10):
    """Benjamini-Hochberg. Returns a list of booleans aligned with pvals."""
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    k_max = 0
    for rank, i in enumerate(order, 1):
        if pvals[i] <= q * rank / m:
            k_max = rank
    keep = set(order[:k_max])
    return [i in keep for i in range(m)]


# =============================================================================
# registry
# =============================================================================

# `invalid`: a measurement found to be wrong BY CONSTRUCTION (e.g. look-ahead in
# its own definition). Kept in the registry and counted in the summary, never in
# BH and never as a replication - whatever its sign.
ROLES = ("search", "replication", "descriptive", "invalid")


class Registry:
    """Append-only JSONL of every interval computed. One file per module, so
    concurrent modules never write the same file."""

    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    def add(self, family, name, res, role="search", unit="pp", population="nfl_wk1",
            note="", scale=1.0):
        if role not in ROLES:
            raise ValueError(role)
        # `ts` is when the interval was COMPUTED. Bar 1 compares it with the
        # commit time of the candidates doc: a replication record older than
        # that commit is a look at the holdout before the mechanism was frozen.
        rec = {"family": family, "name": name, "role": role, "unit": unit,
               "population": population, "note": note, "estimable": bool(res),
               "ts": __import__("time").time()}
        if res:
            rec.update({k: (res[k] * scale if k in ("est", "lo", "hi", "se") else res[k])
                        for k in ("est", "lo", "hi", "se", "p", "n", "games")})
            rec["readable"] = res["games"] >= MIN_GAMES_TO_READ
            rec["excludes_zero"] = res["lo"] > 0 or res["hi"] < 0
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        return rec


if POPULATION == "nfl_wk2":
    open_population()


def load_registries(paths):
    out = []
    for p in paths:
        with open(p, encoding="utf-8") as f:
            out += [json.loads(line) for line in f if line.strip()]
    return out


def bh_p(r):
    """The p-value BH sees. The pre-registered 'not read' rule applied
    mechanically: an interval on < MIN_GAMES_TO_READ games, or with zero
    bootstrap variance, enters BH at p = 1. Raw `p` is kept for display.
    This can only make BH stricter."""
    if r.get("se") in (None, 0) or r.get("games", 0) < MIN_GAMES_TO_READ:
        return 1.0
    return r["p"]


def money_direction(r):
    """'money+' / 'money-' for tests whose estimate IS a net or PnL per contract,
    'stat' for everything else (slopes, deviations, contrasts)."""
    name = (r.get("family", "") + " " + r.get("name", "")).lower()
    if any(k in name for k in ("net", "pnl", "economic")):
        return "money+" if r.get("est", 0) > 0 else "money-"
    return "stat"


def summarize(records, q=0.10, alpha=0.05):
    search = [r for r in records if r["role"] == "search" and r["estimable"]]
    keep = bh([bh_p(r) for r in search], q)
    for r, k in zip(search, keep):
        r["bh_survives"] = k
    return {"search_tests": len(search),
            "search_not_estimable": sum(1 for r in records if r["role"] == "search" and not r["estimable"]),
            "nominal_p_below_alpha": sum(1 for r in search if r["p"] < alpha),
            "expected_false_positives": alpha * len(search),
            "bh_survivors": sum(keep),
            "bh_survivors_money_positive": sum(1 for r, k in zip(search, keep) if k and money_direction(r) == "money+"),
            "bh_survivors_money_negative": sum(1 for r, k in zip(search, keep) if k and money_direction(r) == "money-"),
            "bh_survivors_statistical": sum(1 for r, k in zip(search, keep) if k and money_direction(r) == "stat"),
            "search_unreadable_or_zero_var": sum(1 for r in search if bh_p(r) == 1.0 and r["p"] < 1.0),
            "replication_tests": sum(1 for r in records if r["role"] == "replication"),
            "invalid_by_construction": sum(1 for r in records if r["role"] == "invalid"),
            "descriptive": sum(1 for r in records if r["role"] == "descriptive")}
