"""F13: the frozen grading rule for the fantasy buy-low / sell-high board.

    python -m research.f13_call_grading              # print the rule, refuse to grade
    python -m research.f13_call_grading --ledger X   # (future) grade a call ledger

THIS IS A RULE, NOT A MEASUREMENT. It was committed with
`docs/briefs/f13-fantasy-calls-preregistration.md` before any call existed, and
nothing here reads data. The document is the argument; this module is the part of
it a grading run must import rather than re-type, so the rule and the run cannot
drift apart. Every constant below is named in the document, and
`tests/test_f13_call_grading.py` asserts the two agree.

The grading run itself is NOT built here, on purpose: it needs a call ledger, and a
ledger needs a board, and neither exists at registration. Until one does, `main()`
exits 2 - a script that reports nothing and succeeds is a failed script.

Three verdicts, and every one of them is reachable (the falsifiability rule -
`tests/test_f13_call_grading.py` drives `verdict()` to each):

    supported     the SIMULTANEOUS upper bound on the excess carried fraction
                  is below TAU: at most a quarter of the band's residual carried
    failed        the UNADJUSTED lower bound is above TAU: materially more than
                  a quarter carried. Published and kept, never removed
    inconclusive  neither

Multiplicity is ASYMMETRIC and both halves lean against the board: `supported`
must clear a Bonferroni-widened interval, `failed` needs only the plain 95% one.
Because the claim under test is a NULL ("the residual does not persist"), the usual
correction would make the board harder to fail - which is the direction a correction
must never be allowed to help.
"""
import argparse
import sys

# ---------------------------------------------------------------- the call

# The graded scoring system, pinned BY VALUE. A preset key is a name, and a name can
# be re-pointed at new weights; grading "ppr" after its weights moved would grade a
# different board under the same label. test_f13 asserts these equal the export's
# preset today; if the export's preset changes, that test fails and a new call
# series starts (document section 9) - the old series keeps these weights.
PRESET = "ppr"
PRESET_WEIGHTS = {"rec": 1, "rec_yds": 0.1, "rec_td": 6, "rush_yds": 0.1, "rush_td": 6,
                  "pass_yds": 0.04, "pass_td": 4, "int": -2, "fum_lost": -2,
                  "two_pt": 2, "ret_td": 6}

BANDS = ("sell_high", "buy_low")
SEASON_TYPE = "REG"

# ---------------------------------------------------------------- horizons

# next: the player's next REG game in the same season.
# ros:  every remaining REG game he plays in the same season.
HORIZONS = ("next", "ros")

# ---------------------------------------------------------------- the estimand

# Carried fraction: mean over calls of the per-game residual in the horizon, divided
# by the mean over calls of the per-game residual at the call. For the contrast,
# numerator and denominator are each (sell_high mean - buy_low mean).
# EXCESS carried fraction = real minus the median of the null world's, so mechanics
# of the construction (selection, refit, discreteness, ties, league drift) are
# measured and removed rather than argued away.
TAU = 0.25

ALPHA = 0.05
PRIMARY = tuple(("contrast", h) for h in HORIZONS)                  # 2 tests
SECONDARY = tuple((b, h) for b in BANDS for h in HORIZONS)          # 4 tests

BOOT_DRAWS = 2000          # player-clustered bootstrap
NULL_REPS = 1000           # pooled-redraw null worlds
MIN_PLAYERS_PER_BAND = 30  # distinct players per band per season, else inconclusive
UNGRADABLE_GAP_FLAG = 0.10 # ungradable share differing by more than this between bands


class NoLedger(RuntimeError):
    """Grading was asked for and no call ledger exists."""


def carried_fraction(call_mean, future_mean):
    """future / call. Refuses a zero denominator rather than returning inf or 0:
    a band whose call residual averages zero made no call."""
    if call_mean == 0:
        raise ValueError("call-period residual averages zero; there is no call to carry")
    return future_mean / call_mean


def contrast(sell_mean, buy_mean):
    return sell_mean - buy_mean


def simultaneous_level(family):
    """Two-sided coverage for a `supported` verdict inside a family of tests."""
    if family not in ("primary", "secondary"):
        raise ValueError("family is 'primary' or 'secondary', got %r" % (family,))
    k = len(PRIMARY) if family == "primary" else len(SECONDARY)
    return 1 - ALPHA / k


def verdict(simul, unadj, n_players_by_band, tau=TAU):
    """The verdict for one test, from two intervals on the EXCESS carried fraction.

    `simul` is the Bonferroni-widened interval (`simultaneous_level`), `unadj` the
    plain 95% one; both come from the same bootstrap, so `simul` must contain
    `unadj` - if it does not, the caller has swapped them, and that raises rather
    than grading. `n_players_by_band` maps each band to its distinct players.

    Returns (verdict, reason). The reason is the sentence a page states beside it.
    """
    (slo, shi), (ulo, uhi) = simul, unadj
    if not (slo <= ulo <= uhi <= shi):
        raise ValueError("the simultaneous interval %r must contain the unadjusted %r"
                         % (simul, unadj))
    thin = sorted(b for b, n in n_players_by_band.items() if n < MIN_PLAYERS_PER_BAND)
    if thin:
        return ("inconclusive",
                "fewer than %d distinct players in %s" % (MIN_PLAYERS_PER_BAND,
                                                           ", ".join(thin)))
    if ulo > tau:
        return ("failed", "more than %.2f of the residual carried (lower bound %.3f)"
                % (tau, ulo))
    if shi < tau:
        return ("supported", "at most %.2f of the residual carried (upper bound %.3f)"
                % (tau, shi))
    return ("inconclusive", "the interval [%.3f, %.3f] straddles %.2f" % (slo, shi, tau))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ledger", help="a call ledger (does not exist at registration)")
    args = ap.parse_args(argv)
    print("F13 grading rule: preset %s, bands %s, horizons %s, tau %.2f, "
          "primary %d tests at %.4f simultaneous, secondary %d at %.4f"
          % (PRESET, "/".join(BANDS), "/".join(HORIZONS), TAU,
             len(PRIMARY), simultaneous_level("primary"),
             len(SECONDARY), simultaneous_level("secondary")))
    if not args.ledger:
        print("no call ledger: nothing has been called, so nothing is graded", file=sys.stderr)
        return 2
    raise NoLedger("the grading run is built once a call ledger exists "
                   "(docs/briefs/f13-fantasy-calls-preregistration.md section 10)")


if __name__ == "__main__":
    raise SystemExit(main())
