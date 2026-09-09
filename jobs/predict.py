"""Write timestamped, immutable predictions for mapped outcomes.

    python -m jobs.predict --season 2026 --week 1
    python -m jobs.predict --season 2026 --week 1 --dry-run
    python -m jobs.predict --report            # what was written, vs kickoff

Every row is written BEFORE the relevant kickoff or not at all. That is not a
nicety: a "prediction" stamped after the game started is the single most
convincing wrong number a system like this can produce, because it will look
brilliant in every evaluation that follows.

The as-of contract is enforced twice - once in SQL, where features cannot see
past `as_of_ts`, and once here, where `Provenance.assert_as_of` refuses to write
a row whose inputs postdate it.
"""
import argparse
import sqlite3
import time
from collections import defaultdict

import config
import store
from models import baseline, features


def code_fingerprint() -> str:
    from run_logger import code_fingerprint as fp
    return fp()


def _pending_outcomes(con, season, week, as_of_ts, venue="kalshi",
                      require_pre_kickoff=True):
    """Mapped player outcomes whose game has NOT started yet."""
    q = """
        SELECT o.outcome_id, o.entity_id, o.stat, o.line, o.push_possible,
               o.event_id, mo.market_id, g.kickoff_ts, x.position, x.last_team
          FROM outcomes o
          JOIN market_outcome mo ON mo.outcome_id = o.outcome_id
          LEFT JOIN (SELECT game_id, MAX(kickoff_ts) kickoff_ts
                       FROM nfl_games GROUP BY game_id) g
            ON g.game_id = o.event_id
          LEFT JOIN player_xwalk x ON x.gsis_id = o.entity_id
         WHERE mo.venue = ? AND o.season = ? AND o.week = ?
           AND o.entity_type = 'player' AND o.stat IS NOT NULL
    """
    args = [venue, season, week]
    if require_pre_kickoff:
        q += " AND g.kickoff_ts > ?"
        args.append(as_of_ts)
    return con.execute(q, args).fetchall()


def run(season=2026, week=1, as_of_ts=None, dry_run=False, venue="kalshi"):
    as_of_ts = as_of_ts or time.time()
    fingerprint = code_fingerprint()
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)

    rows = _pending_outcomes(con, season, week, as_of_ts, venue)
    skipped = _pending_outcomes(con, season, week, as_of_ts, venue,
                                require_pre_kickoff=False)
    started = len(skipped) - len(rows)

    # ONE fit per (player, stat); every line is a query against it (invariant
    # #4). 1,049 outcomes collapse to ~200 fits because the venues quote a
    # ladder of thresholds on the same underlying claim.
    fits = {}
    written = 0
    errors = defaultdict(int)
    for (_oid, gsis, stat, _line, _push, _event, _mid, _kick, position,
         last_team) in rows:
        key = (gsis, stat)
        if key in fits:
            continue
        try:
            fits[key] = baseline.fit_player_stat(
                con, gsis, stat, season, as_of_ts, position, last_team)
        except Exception as e:
            fits[key] = None
            errors[f"{type(e).__name__}: {e}"[:80]] += 1

    now = time.time()
    for (oid, gsis, stat, line, push, _event, _mkt, kickoff, _pos,
         _team) in rows:
        fit = fits.get((gsis, stat))
        if fit is None:
            continue
        # The guard, per row, before anything is written.
        fit.provenance.assert_as_of(as_of_ts)
        if kickoff is not None and as_of_ts >= kickoff:
            errors["as_of at or after kickoff"] += 1
            continue

        push_flag = bool(push)
        prob = fit.dist.prob_over(float(line), push=push_flag)
        push_prob = (fit.dist.prob_mass_at(float(line))
                     if push_flag and hasattr(fit.dist, "prob_mass_at") else 0.0)
        if dry_run:
            written += 1
            continue
        store.record_prediction({
            "outcome_id": oid,
            "model_version": baseline.MODEL_VERSION,
            "code_fingerprint": fingerprint,
            "as_of_ts": as_of_ts,
            "created_ts": now,
            "family": fit.family,
            "params_json": fit.params_json(),
            "mean": fit.dist.mean(),
            "prob_over": prob,
            "push_prob": push_prob,
            "prior_games": fit.prior_games,
            "shrink_weight": fit.shrink_weight,
        })
        written += 1

    con.close()
    store.record_health("predictions", not errors,
                        f"{written} predictions, {len(fits)} fits, "
                        f"{started} outcomes skipped (game already started)",
                        watermark=as_of_ts)
    return {"written": written, "fits": len(fits), "started": started,
            "as_of_ts": as_of_ts, "errors": dict(errors)}


def report(season=2026, week=1):
    """The acceptance print: counts, and as_of against every kickoff."""
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    row = con.execute(
        """SELECT COUNT(*), COUNT(DISTINCT p.outcome_id), MIN(p.as_of_ts),
                  MAX(p.as_of_ts), MIN(p.prior_games), MAX(p.prior_games)
             FROM predictions p JOIN outcomes o USING (outcome_id)
            WHERE o.season = ? AND o.week = ?""", (season, week)).fetchone()
    n, n_out, min_as_of, max_as_of, min_pg, max_pg = row
    if not n:
        print("no predictions")
        return 1
    print(f"predictions: {n:,} rows over {n_out:,} outcomes")
    print(f"as_of_ts   : {min_as_of:.0f} .. {max_as_of:.0f} "
          f"(span {(max_as_of - min_as_of):.0f}s)")
    print(f"prior_games: {min_pg} .. {max_pg}")
    print()
    print(f"{'game':<22} {'kickoff':>12} {'preds':>6} {'latest as_of':>13} "
          f"{'lead':>9}")
    bad = 0
    for gid, kick, cnt, latest in con.execute(
            """SELECT o.event_id, MAX(g.kickoff_ts), COUNT(*), MAX(p.as_of_ts)
                 FROM predictions p
                 JOIN outcomes o USING (outcome_id)
                 JOIN nfl_games g ON g.game_id = o.event_id
                WHERE o.season = ? AND o.week = ?
                GROUP BY o.event_id ORDER BY MAX(g.kickoff_ts)""",
            (season, week)):
        lead = (kick - latest) / 3600.0
        flag = ""
        if lead <= 0:
            flag, bad = "  <-- AFTER KICKOFF", bad + 1
        print(f"{gid:<22} {kick:>12.0f} {cnt:>6,} {latest:>13.0f} "
              f"{lead:>8.1f}h{flag}")
    con.close()
    print()
    print(f"every prediction leads its kickoff: {'NO' if bad else 'YES'}")
    return bad


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--week", type=int, default=1)
    ap.add_argument("--venue", default="kalshi")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()

    store.init_db()
    if args.report:
        raise SystemExit(1 if report(args.season, args.week) else 0)
    r = run(args.season, args.week, dry_run=args.dry_run, venue=args.venue)
    print(f"written={r['written']} fits={r['fits']} "
          f"skipped_started={r['started']} as_of={r['as_of_ts']:.0f}")
    for e, c in r["errors"].items():
        print(f"  {c:>5}  {e}")


if __name__ == "__main__":
    main()
