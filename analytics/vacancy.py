"""6. Vacancy: where a missing player's usage goes, as a share of what he left.

    python -m analytics.vacancy --publish
    python -m analytics.vacancy --show            # the published figures
    python -m analytics.vacancy --events          # counts at every filter

THE QUESTION. A starter is out. Every site says so; none says, with a number
and an interval, who absorbs his work. This measures it over every absence the
archive can see, per position group, as the share of the VACATED share each
teammate depth slot absorbs - not a raw delta, because a 24% target share and
an 11% one leave different holes.

THE DEFINITIONS, PRE-REGISTERED IN THE BRIEF (a-17) AND NOT TUNED AFTER:

  meaningful role   >= 15% of team targets OR >= 40% of team offensive snaps,
                    averaged over his last 4 appearances
  absent            no row for him in that team-game, the team having played.
                    "Row" means a snap-count row - any snaps at all, special
                    teams included, so a player who returns mid-game or dresses
                    for kick coverage is NOT absent (the brief's third lie).
  absorption        for each teammate at the same position group, his share in
                    the absence game minus his own last-4 baseline, summed over
                    events and divided by the summed vacated share

DECISIONS TAKEN HERE, BEFORE ANY RESULT WAS READ (a-17, unattended). Each is a
choice the brief left open; each is written down so it can be reversed.

  * ABSENCE IS READ FROM `nfl_snap_counts`, SO THE RANGE STARTS IN 2013. The
    play-by-play names only players who touched the ball, and `stats_player_week`
    has no row for a player who played without recording a stat (CLAUDE.md,
    verified 2026-09-15). Before 2013 "absent" and "dressed, never targeted"
    are the same observation, so an earlier start would count healthy decoys
    as vacancies. Declared as `floor_season`, because the survey does not
    measure the snap feed.
  * LAST 4 APPEARANCES ARE WITHIN ONE TEAM-SEASON. A role from last season on
    another roster is not the role that just vacated. It costs every absence in
    a team's first four games.
  * ONLY THE FIRST GAME OF AN ABSENCE SPELL COUNTS. In the second consecutive
    game the teammates' own last-4 baselines already contain the absence, so
    their deltas would measure the vacancy against itself.
  * NOT ABSENT IF HE PLAYED FOR ANOTHER TEAM THAT WEEK - a trade is not an
    absence. A release or IR stint IS counted: the vacancy is the same.
  * A TEAM-GAME WITH TWO OR MORE QUALIFYING ABSENCES IS EXCLUDED ENTIRELY. With
    two holes there is no way to say which one a teammate filled.
  * GROUPS: WR, TE, RB (RB includes FB and HB). Quarterbacks are left out: a
    backup QB inherits ~all of the snaps by construction and there is nothing
    to measure. Carries are published for the RB group only.
  * A teammate with no prior appearance for the team that season has a
    baseline of 0 - a call-up absorbing work is exactly the thing measured.

THE BRIEF'S SECOND LIE: DEPTH SLOTS ARE NOT STABLE IDENTITIES. Slots are ranked
by each teammate's own preceding-4 share OF THE MEASURE BEING ABSORBED - by
target share for the targets metric, snap share for snaps - never by a roster
label, and ties broken by baseline snap share. So "slot 1" for targets and
"slot 1" for snaps can be different players in the same event.

A FOURTH LIE THE BRIEF DID NOT NAME, AND IT IS MECHANICAL. Ranking teammates by
their baseline and then measuring their change from it is regression to the
mean by construction: the top-ranked teammate's baseline is selected high, so
he regresses DOWN with nobody missing at all, and the bottom slot drifts up.
So every slot is also measured on a PLACEBO - team-games where a qualifying
player DID play, same ranking, same baselines, his own share left out - and the
`net` slice is absence minus placebo, bootstrapped as ONE quantity over shared
team-season blocks (brief 018: never the difference of two separately quoted
intervals). `net` is the figure to quote.

THE INTERVAL is a block bootstrap over TEAM-SEASONS. Absences within a
team-season share a roster, a coordinator and often the same injured player,
so they are not independent; `n` is team-seasons and `rows` is events. Each
slice is resampled with its OWN draws (subject = metric|slice), because slot 1
beside slot 2 is precisely the comparison a reader makes and shared draws would
correlate their Monte Carlo error (CLAUDE.md, *Shared denominators*).

THE FIRST LIE, SELECTION, IS STATED AND NOT CORRECTED. A player misses because
something happened, and a team changes its plan for a missing starter rather
than only reallocating his share. That is in every metric's `range_note`.
"""
import argparse
import sqlite3
import sys
import zlib
from collections import Counter, defaultdict

from analytics import metrics, paths
from analytics.intervals import block_bootstrap, histogram_bootstrap

# ---- pre-registered in the brief. Do not tune these after seeing a result. --
ROLE_TARGET_SHARE = 0.15
ROLE_SNAP_SHARE = 0.40
BASELINE_APPEARANCES = 4
# -----------------------------------------------------------------------------

SNAP_FIRST_SEASON = 2013     # nfl_snap_counts' own first season
SLOTS = 3
MIN_BLOCKS = 5               # brief 020: fewer blocks than this is not read
QUANTILES = (("p25", 0.25), ("p50", 0.50), ("p75", 0.75))

GROUPS = {"WR": "WR", "TE": "TE", "RB": "RB", "FB": "RB", "HB": "RB"}

# nfl_snap_counts writes the franchise's abbreviation AT THE TIME; the spine
# writes today's. Measured: these three are the whole difference 2013-2025.
TEAM_ALIAS = {"STL": "LA", "SD": "LAC", "OAK": "LV"}

MEASURES = {
    # measure -> (groups published, the role criterion that makes a per-event
    #             ratio meaningful for the distribution slices, or None)
    "targets": (("WR", "TE", "RB"), "targets"),
    "snaps": (("WR", "TE", "RB"), "snaps"),
    "carries": (("RB",), None),
}

CAVEAT = (
    "Absence is read from snap counts: no row at all for the player in that "
    "team-game, the team having played, and not on another roster that week; "
    "only the first game of an absence spell counts, and a team-game with two "
    "qualifying absences is dropped. SELECTION IS NOT CORRECTED: a player "
    "misses because something happened, and a team changes its plan for a "
    "missing starter rather than only redistributing his share, so this is "
    "what historically happened after an absence, not what an absence causes. "
    "Depth slots are ranked by each teammate's own preceding-4 share of this "
    "measure, not by any roster label, so slot 1 is whoever led the measure "
    "among the teammates who dressed. Ranking on a baseline builds in "
    "regression to the mean; the `net` slices subtract a placebo measured on "
    "games where the role player did play.")


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------

def _group(position):
    if not position:
        return None
    return GROUPS.get(position.split("/")[0].strip().upper())


def load(mcon, acon, season_from, season_to):
    """(appearances, team_totals, present_any).

    appearances  [(season, week, game_id, team, pfr_id, group, snap_share,
                   targets, carries)] - one per skill-position snap row
    team_totals  {(game_id, team): (targets, carries)} from the spine
    present_any  {(pfr_id, season, week)} for EVERY snap row, any position,
                 any team - the trade check
    """
    xwalk = dict(mcon.execute(
        "SELECT pfr_id, gsis_id FROM player_xwalk WHERE pfr_id IS NOT NULL"))
    # Latest data_version per (player, game): corrections are re-issued as new
    # versions, and 2,901 pairs carry more than one.
    rows = mcon.execute(
        "SELECT s.season, s.week, s.game_id, s.team, s.pfr_player_id, "
        "s.position, s.offense_pct FROM nfl_snap_counts s JOIN ("
        "  SELECT pfr_player_id, game_id, MAX(data_version) AS v "
        "  FROM nfl_snap_counts WHERE season BETWEEN ? AND ? "
        "  GROUP BY pfr_player_id, game_id) l "
        "ON s.pfr_player_id = l.pfr_player_id AND s.game_id = l.game_id "
        "AND s.data_version = l.v", (season_from, season_to)).fetchall()
    usage = {}
    for game, pid, tgt, car in acon.execute(
            "SELECT game_id, player_id, SUM(is_target), SUM(is_carry) "
            "FROM f_play_usage WHERE season BETWEEN ? AND ? "
            "AND role IN ('rusher','receiver') GROUP BY game_id, player_id",
            (season_from, season_to)):
        usage[(game, pid)] = (tgt or 0, car or 0)
    team_totals = {}
    for game, team, tgt, car in acon.execute(
            "SELECT game_id, team, SUM(is_target), SUM(is_carry) "
            "FROM f_play_usage WHERE season BETWEEN ? AND ? "
            "AND role IN ('rusher','receiver') GROUP BY game_id, team",
            (season_from, season_to)):
        team_totals[(game, team)] = (tgt or 0, car or 0)
    present_any, apps, unmapped = set(), [], set()
    team_games = set()
    for season, week, game, team, pfr, pos, pct in rows:
        team = TEAM_ALIAS.get(team, team)
        present_any.add((pfr, season, week))
        team_games.add((season, week, game, team))
        grp = _group(pos)
        if grp is None:
            continue
        gsis = xwalk.get(pfr)
        if gsis is None:
            unmapped.add(pfr)
        tgt, car = usage.get((game, gsis), (0, 0))
        apps.append((season, week, game, team, pfr, grp, pct, tgt, car))
    return apps, team_totals, present_any, team_games, unmapped


# ---------------------------------------------------------------------------
# events - pure, so the tests can drive it with a hand-built season
# ---------------------------------------------------------------------------

def _share(rec, measure, team_totals):
    season, week, game, team, pfr, grp, pct, tgt, car = rec
    if measure == "snaps":
        return pct
    tot = team_totals.get((game, team))
    if not tot:
        return None
    num, den = (tgt, tot[0]) if measure == "targets" else (car, tot[1])
    return (num / den) if den else None


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return (sum(xs) / len(xs)) if xs else None


def find_events(apps, team_totals, present_any, team_games):
    """(absences, placebos, counts).

    Each event is a dict: season, team, game, pfr, group, qual_targets,
    qual_snaps, and `m` = {measure: (vacated, [d_slot1..d_slotN], d_group,
    d_other)}, where every d is the summed share change of those teammates.
    """
    schedule = defaultdict(list)             # (season, team) -> [(week, game)]
    for season, week, game, team in team_games:
        schedule[(season, team)].append((week, game))
    at = defaultdict(dict)                   # (season, team, game) -> {pfr: rec}
    for rec in apps:
        at[(rec[0], rec[3], rec[2])][rec[4]] = rec

    counts = Counter()
    candidates, placebo_raw = [], []
    # SORTED, because `team_games` is a set and string hashing is salted per
    # process: iterating it raw put blocks into the bootstrap in a different
    # order every run, so the same data gave intervals differing in the third
    # decimal. The point estimates were identical, which is why only a second
    # run could show it.
    for (season, team), games in sorted(schedule.items()):
        games.sort()
        history = defaultdict(list)          # pfr -> [rec] in game order
        for gi, (week, game) in enumerate(games):
            here = at.get((season, team, game), {})
            prev = at.get((season, team, games[gi - 1][1]), {}) if gi else {}
            # everyone who has appeared for this team this season
            for pfr, past in sorted(history.items()):
                if len(past) < BASELINE_APPEARANCES:
                    continue
                last = past[-BASELINE_APPEARANCES:]
                base_t = _mean(_share(r, "targets", team_totals) for r in last)
                base_s = _mean(r[6] for r in last)
                if not ((base_t or 0) >= ROLE_TARGET_SHARE
                        or (base_s or 0) >= ROLE_SNAP_SHARE):
                    continue
                if pfr not in prev:
                    continue                 # not the first game of a spell
                grp = Counter(r[5] for r in last).most_common(1)[0][0]
                ev = {"season": season, "team": team, "game": game, "gi": gi,
                      "pfr": pfr, "group": grp,
                      "qual_targets": (base_t or 0) >= ROLE_TARGET_SHARE,
                      "qual_snaps": (base_s or 0) >= ROLE_SNAP_SHARE}
                if pfr in here:
                    placebo_raw.append(ev)
                    continue
                counts["role player missing from a team-game"] += 1
                if (pfr, season, week) in present_any:
                    counts["  on another roster that week (traded): dropped"] += 1
                    continue
                candidates.append(ev)
            for pfr, rec in here.items():
                history[pfr].append(rec)
    per_game = Counter((e["season"], e["team"], e["game"]) for e in candidates)
    absences = []
    for e in candidates:
        if per_game[(e["season"], e["team"], e["game"])] > 1:
            counts["  in a team-game with 2+ qualifying absences: dropped"] += 1
            continue
        absences.append(e)
    counts["absence events kept"] = len(absences)
    busy = set(per_game)
    placebos = [e for e in placebo_raw
                if (e["season"], e["team"], e["game"]) not in busy]
    counts["placebo events (role player present, no absence that game)"] = \
        len(placebos)

    # teammate deltas
    for e in absences + placebos:
        games = schedule[(e["season"], e["team"])]
        gi = e["gi"]
        here = at.get((e["season"], e["team"], e["game"]), {})
        prior = defaultdict(list)
        for _w, g in games[:gi]:
            for pfr, rec in at.get((e["season"], e["team"], g), {}).items():
                prior[pfr].append(rec)
        vac_last = prior[e["pfr"]][-BASELINE_APPEARANCES:]
        e["m"] = {}
        for measure in MEASURES:
            vac = _mean(_share(r, measure, team_totals) for r in vac_last)
            if vac is None or vac <= 0:
                continue
            mates = []
            ok = True
            for pfr, rec in sorted(here.items()):
                if pfr == e["pfr"]:
                    continue
                now = _share(rec, measure, team_totals)
                if now is None:
                    ok = False
                    break
                last = prior.get(pfr, [])[-BASELINE_APPEARANCES:]
                base = _mean(_share(r, measure, team_totals) for r in last) or 0.0
                base_snap = _mean(r[6] for r in last) or 0.0
                mates.append((rec[5], base, base_snap, pfr, now - base))
            if not ok:
                continue
            same = sorted((m for m in mates if m[0] == e["group"]),
                          key=lambda m: (-m[1], -m[2], m[3]))
            slots = [same[k][4] if k < len(same) else 0.0 for k in range(SLOTS)]
            d_group = sum(m[4] for m in same)
            d_other = sum(m[4] for m in mates if m[0] != e["group"])
            e["m"][measure] = (vac, slots, d_group, d_other)
    return absences, placebos, counts


# ---------------------------------------------------------------------------
# aggregation
# ---------------------------------------------------------------------------

STAT_NAMES = ["slot%d" % (k + 1) for k in range(SLOTS)] + ["group", "other"]


def _vector(ev, measure):
    vac, slots, d_group, d_other = ev["m"][measure]
    return [1.0, vac] + list(slots) + [d_group, d_other]


def _blocks(events, measure, group):
    import numpy as np
    out = {}
    for e in events:
        if e["group"] != group or measure not in e["m"]:
            continue
        v = np.array(_vector(e, measure))
        key = (e["season"], e["team"])
        out[key] = out[key] + v if key in out else v
    return out


def _subject_seed(metric_key, slice_key):
    return "%s|%s" % (metric_key, slice_key)


def aggregate(absences, placebos, measure, metric_key):
    """[('_league', slice, Estimate)] for one measure, every group."""
    import numpy as np
    groups, qual = MEASURES[measure]
    out = []
    for group in groups:
        ab = _blocks(absences, measure, group)
        # THE PLACEBO IS MATCHED: only team-seasons that carry an absence in
        # this group, so `placebo` and `net` share one population and a reader
        # subtracting the two published slices gets the `net` point estimate.
        pl = {k: v for k, v in _blocks(placebos, measure, group).items()
              if k in ab}
        if len(ab) < MIN_BLOCKS:
            continue

        def one(blocks, name, fn):
            sl = name
            got = histogram_bootstrap(
                blocks, {sl: fn}, rows_by_block={k: blocks[k][0] for k in blocks},
                subject=_subject_seed(metric_key, "%s|%s" % (group, sl)))
            est = got[sl]
            if est.est is not None:
                out.append(("_league", "%s|%s" % (group, sl), est))

        one(ab, "vacated", lambda v: v[1] / v[0] if v[0] else None)
        for i, name in enumerate(STAT_NAMES):
            j = 2 + i
            one(ab, name, lambda v, j=j: v[j] / v[1] if v[1] else None)
            if len(pl) >= MIN_BLOCKS:
                one(pl, name + "|placebo",
                    lambda v, j=j: v[j] / v[1] if v[1] else None)
                # NET: one quantity, one set of resamples over the absence
                # team-seasons. `rows` is absence events; a block with no
                # placebo contributes zeros to that half.
                width = len(STAT_NAMES) + 2
                both = {k: np.concatenate([a, pl.get(k, np.zeros(width))])
                        for k, a in ab.items()}
                one(both, name + "|net",
                    lambda v, j=j, w=width: (v[j] / v[1] - v[w + j] / v[w + 1])
                    if v[1] and v[w + 1] else None)
        if qual is None:
            continue
        # THE DISTRIBUTION, per event: what share of the hole slot k took in
        # one game. Only events where the player qualified on THIS measure's
        # own criterion - a blocking tight end's 3% target share is a hole too
        # small for a per-event ratio to mean anything.
        per_block = defaultdict(list)
        for e in absences:
            if e["group"] != group or measure not in e["m"] or not e["qual_" + qual]:
                continue
            vac, slots, d_group, d_other = e["m"][measure]
            per_block[(e["season"], e["team"])].append(
                [d / vac for d in list(slots) + [d_group, d_other]])
        if len(per_block) < MIN_BLOCKS:
            continue
        for i, name in enumerate(STAT_NAMES):
            blocks = {k: [r[i] for r in v] for k, v in per_block.items()}
            for qname, q in QUANTILES:
                sl = "%s|%s|%s" % (group, name, qname)
                seed = zlib.crc32(_subject_seed(metric_key, sl).encode("utf-8"))
                est = block_bootstrap(
                    blocks, lambda xs, q=q: _quantile(xs, q), seed=seed)
                if est.est is not None:
                    out.append(("_league", sl, est))
    return out


def _quantile(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    pos = q * (len(xs) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------

REQUIRES = {
    # Same conditions `analytics.script` uses: a target is at risk of going
    # unattributed on an INCOMPLETION, and 2003-2008 name the receiver there on
    # under 1% of plays. Snap counts start later than both, so they bind.
    "targets": (("pbp", "receiver_player_id", "incomplete_pass"),
                ("pbp", "receiver_player_id", "pass_attempt")),
    "carries": (("pbp", "rusher_player_id", "rush_attempt"),),
    "snaps": (),
}


def metric_for(measure):
    return metrics.Metric(
        key="vacancy.%s" % measure,
        label="Where a missing player's %s go" % measure,
        unit=("share of the absent player's vacated %s share (his mean share "
              "of team %s over his last 4 appearances) that teammates at his "
              "position absorb in the game he misses, summed over absences; "
              "`vacated` slices are the mean vacated share itself"
              % ({"targets": "target", "snaps": "snap",
                  "carries": "carry"}[measure], measure)),
        subject_type="league", block="team", basis="pbp",
        availability="current",
        slice_kind=("position group | depth slot (slot1..slot3 by own "
                    "preceding-4 share, group = every same-position teammate, "
                    "other = every other WR/TE/RB) | nothing for the pooled "
                    "absorption, placebo, net (absence minus placebo), or "
                    "p25/p50/p75 of the per-event absorption"),
        shares_denominator="team",
        requires=REQUIRES[measure],
        floor_season=SNAP_FIRST_SEASON,
        floor_reason=("nfl_snap_counts, the only record of who dressed (it "
                      "starts in 2013)"),
        note=CAVEAT)


def market_log_ro(path=None):
    if path is None:
        return paths.market_log_ro()
    return sqlite3.connect(paths._uri(path) + "?mode=ro", uri=True, timeout=5)


def compute(acon, mcon, season_from, season_to):
    apps, totals, present, team_games, unmapped = load(
        mcon, acon, season_from, season_to)
    absences, placebos, counts = find_events(apps, totals, present, team_games)
    counts["skill-position snap ids with no crosswalk (usage read as 0)"] = \
        len(unmapped)
    return absences, placebos, counts


def publish(acon, mcon, verbose=True):
    written = {}
    ranges = {m: metrics.derive_range(acon, metric_for(m)) for m in MEASURES}
    lo = min(r[0] for r in ranges.values())
    hi = max(r[1] for r in ranges.values())
    absences, placebos, counts = compute(acon, mcon, lo, hi)
    if verbose:
        for k, v in counts.items():
            print("  %-62s %7d" % (k, v))
    if not absences:
        raise SystemExit("no absence events - refusing to publish an empty "
                         "metric. An analytic that measured nothing and exits 0 "
                         "is a failed analytic.")
    for measure in MEASURES:
        m = metric_for(measure)
        s0, s1, note = ranges[measure]
        ab = [e for e in absences if s0 <= e["season"] <= s1]
        pl = [e for e in placebos if s0 <= e["season"] <= s1]
        rows = aggregate(ab, pl, measure, m.key)
        written[m.key] = metrics.publish(acon, m, rows, s0, s1)
        if verbose:
            print("  %-18s %d-%d  %d values   [%s]"
                  % (m.key, s0, s1, written[m.key], note), flush=True)
    return written


def show(acon):
    rows = acon.execute(
        "SELECT metric, slice, est, lo, hi, n, rows FROM f_metric_values "
        "WHERE metric LIKE 'vacancy.%' ORDER BY metric, slice").fetchall()
    if not rows:
        raise SystemExit("nothing published under vacancy.*")
    for metric, sl, est, lo, hi, n, r in rows:
        print("%-18s %-24s %+.3f [%+.3f, %+.3f]  n=%d team-seasons, %s events"
              % (metric, sl, est, lo, hi, n, r))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--events", action="store_true")
    ap.add_argument("--market-log", help="path to market_log.db, opened mode=ro "
                    "(default: the configured logger database)")
    a = ap.parse_args(argv)
    if a.publish:
        publish(paths.connect(), market_log_ro(a.market_log))
    if a.events:
        acon = paths.connect(read_only=True)
        _ab, _pl, counts = compute(acon, market_log_ro(a.market_log),
                                   SNAP_FIRST_SEASON, metrics.live_season(acon))
        for k, v in counts.items():
            print("  %-62s %7d" % (k, v))
    if a.show:
        show(paths.connect(read_only=True))
    if not (a.publish or a.show or a.events):
        ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
