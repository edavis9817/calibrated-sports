"""Small statistics the whole project shares, so a published number means one
thing wherever it appears.

THE WILSON INTERVAL IS HERE BECAUSE TWO COPIES ALREADY DIVERGED. As of
2026-09-16 `research/longshot.py` clamps its result to [0, 1] and returns
(0.0, 0.0) at n = 0, while `research/sweep/common.py` returns NaN and does not
clamp. Both are defensible inside their own scripts; neither is safe to pick at
random for a number the SITE publishes. This module is the one the export uses.

The research copies are deliberately left alone: their outputs are pinned in
CLAUDE.md, and quietly changing a NaN to a 0.0 underneath a recorded finding is
how a reproducible number stops being reproducible. Unifying them is a separate,
deliberate change with a re-run attached.

Why Wilson at all, rather than a normal approximation: at n = 11 and p = 0.09 a
normal interval runs below zero. Every naive standard error this project has
quoted at small n has been wrong in that direction, and the longshot-bias
finding was corrected precisely because a Wilson interval contained the priced
value where a normal one did not.
"""
import math

from core.settlement import OVER


def wilson(k, n, z=1.96):
    """Wilson score interval for k successes in n trials. -> (lo, hi).

    n = 0 returns (0.0, 0.0): there is no interval, and NaN in a published JSON
    field is not representable anyway - it serialises to something no JSON
    parser accepts. Callers that need "unknown" should omit the field or check
    n themselves rather than read a degenerate interval as a measurement.

    Clamped to [0, 1]. The closed form can overshoot by a float epsilon at
    k = n, and a probability of 1.0000000000000002 on a page is the kind of
    detail that makes a reader doubt every other number on it.
    """
    if n <= 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    centre = p + z * z / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (min(max((centre - half) / d, 0.0), 1.0),
            min(max((centre + half) / d, 0.0), 1.0))


# =============================================================================
# hit rates: one market, one row
# =============================================================================
#
# A settled prop exists TWICE in `outcomes` - once as the over, once as the
# under - and `outcome_settlement.result` records the MARKET's outcome, which is
# identical on both rows. Measured 2026-09-17: 97,948 of 97,948 two-sided
# markets agree on `result`. So a hit rate computed over both rows counts every
# game twice:
#
#     receptions, over rows only    n=21,914   rate 0.4275
#     receptions, under rows only   n=20,031   rate 0.4383
#     receptions, pooled            n=41,945   rate 0.4327
#
# The RATE barely moves. What doubles is `n`, so every interval built on it is
# about sqrt(2) too narrow and the sample looks twice the size it is - the same
# class as brief 021's "the effective sample is 136 fits, not 935".
#
# A SECOND failure shares the shape: asking "did THIS ROW's side win" over both
# sides returns exactly 0.5000 in every slice, because over and under are exact
# complements, and that reads as proof of perfect calibration. One rule stops
# both - count each market once - which is why this is keyed on the MARKET and
# not on the side.
#
# WHY NOT SIMPLY FILTER `side == "over"`. 4,211 markets are one-sided, including
# 1,800 receptions markets carrying only an under row; `anytime_td`,
# `passing_yards` and `rush_yards` have no under rows at all - one-sided by
# construction (CLAUDE.md: `player_anytime_td` is every-outcome-"Yes"). A filter
# would drop them silently. Because `result` is market-level, the over-view is
# recoverable from EITHER row, so this normalises rather than filters.

# One claim, priced at one line, in one game. Two lines on the same player in the
# same game are two claims - a ladder prices several - so `line` is in the key.
MARKET_KEY = ("season", "week", "entity_id", "stat", "line")

# ONE INDEPENDENT OBSERVATION. A ladder's rungs are not independent draws: every
# threshold on a player-game settles off the SAME final stat line, so 6 rungs are
# 6 claims but 1 event. The rate is computed over claims - "how often did a
# posted line clear" - and the INTERVAL is computed over events, because that is
# what the uncertainty is about.
#
# MEASURED 2026-09-17, and it is not a rounding detail:
#
#     player-game-stat cells 38,061   markets 102,159   inflation 2.68x
#     receiving_yards  113,409 markets / 10,767 games = 10.53x
#     receptions        41,945 /         10,293       =  4.08x
#
# On one real player: receiving yards at n=1,046 rungs gives [0.4327, 0.4930];
# the same record on its 53 games gives [0.3438, 0.6034] - 4.3x wider. Publishing
# the first invites a reader to compare two players and see a difference that the
# data cannot support. CLAUDE.md has said this since brief 021 ("the effective
# sample is GAMES, not rungs") and the CFB calibration work collapses to one
# observation per game for the same reason.
GAME_KEY = ("season", "week", "entity_id", "stat")

# Imported, not re-declared. `core.settlement` is the single home of the
# settlement vocabulary and imports nothing, so there is no cycle to dodge - and
# this session's most expensive defect was a rule that existed twice.


class PooledSides(ValueError):
    """A hit-rate population containing the same market more than once."""


def market_key(row, fields=MARKET_KEY):
    return tuple(row[f] for f in fields)


def pooled_side_violations(rows, fields=MARKET_KEY):
    """[(key, sides)] for every market appearing more than once; [] when clean.

    Pure, and returns rather than raises, so a caller can report every offending
    market at once - the same shape as `cfb.guards.scope_violations` and
    `analytics.gate`.
    """
    seen, order = {}, []
    for r in rows:
        k = market_key(r, fields)
        if k not in seen:
            seen[k] = []
            order.append(k)
        seen[k].append(r.get("side"))
    return [(k, sorted(set(seen[k]))) for k in order if len(seen[k]) > 1]


def hit_rate(rows, fields=MARKET_KEY, cluster=GAME_KEY):
    """How often the OVER cleared, counting each market once and each EVENT once.

    -> {"cleared", "n", "games", "rate", "lo", "hi"}
       `n`      claims: posted lines that settled. The record.
       `games`  independent events behind them. The effective sample.
       `rate`   cleared / n - over claims, which is what "how often did a posted
                line clear" means.
       lo, hi   Wilson on GAMES, not on n. `rate`/`lo`/`hi` are None at n = 0,
                because 0/0 is not 0.0 and a degenerate interval must not read
                as a measurement.

    THE INTERVAL IS THE WHOLE POINT OF THE SPLIT. Six rungs on one player-game
    settle off one final stat line, so they carry one game's worth of evidence,
    not six. Computing the interval on `n` makes it ~sqrt(n/games) too narrow -
    measured at 1.64x overall and 3.2x on receiving yards - and the failure is
    invisible, because the RATE is correct and only the confidence is invented.
    Pass `cluster=None` for a population that genuinely has one claim per event.

    RAISES `PooledSides` on a population carrying any market twice. Refusing is
    the point: the pooled number is not obviously wrong - the rate is right and
    only the sample is inflated - so it survives review and publishes a claim
    with an interval it has not earned.
    """
    bad = pooled_side_violations(rows, fields)
    if bad:
        shown = "; ".join(f"{k} appears as {s}" for k, s in bad[:3])
        more = f" (+{len(bad) - 3} more)" if len(bad) > 3 else ""
        raise PooledSides(
            f"{len(bad)} market(s) counted more than once: {shown}{more}. "
            "A hit rate takes one row per market; over and under are the same "
            "event and `result` is identical on both.")
    n = len(rows)
    cleared = sum(1 for r in rows if r.get("result") == OVER)
    if not n:
        return {"cleared": 0, "n": 0, "games": 0, "rate": None, "lo": None, "hi": None}
    games = n if cluster is None else len(
        {tuple(r[f] for f in cluster) for r in rows})
    rate = cleared / n
    # Wilson needs an integer success count, so the rate is carried onto the
    # effective sample rather than the raw one. round() keeps k <= games.
    lo, hi = wilson(min(round(rate * games), games), games)
    return {"cleared": cleared, "n": n, "games": games,
            "rate": rate, "lo": lo, "hi": hi}
