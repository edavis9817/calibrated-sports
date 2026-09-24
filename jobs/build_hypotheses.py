"""docs/hypotheses.json is EMITTED, not typed.

    python -m jobs.build_hypotheses            # write the file
    python -m jobs.build_hypotheses --check    # exit 1 if the file on disk has drifted

WHY THIS EXISTS. `hypotheses.json` was the last hand-typed numeric surface on the
site: seventeen records whose estimates and intervals were transcribed by hand
from CLAUDE.md. Two were already wrong - R10 and R15 both still quoted
pre-settlement figures after the write that moved them, and nothing could have
caught it, because the file was nobody's output. A typed number has no provenance
and cannot go stale loudly.

THE SPLIT. Hand-written here: QUESTION TEXT AND PROVENANCE - the question, the
verdict, the metric's prose, the why, the script. Not hand-written: any figure a
script already computed. That comes from the script's own registry via a
`Pointer`.

A POINTER IS AN IDENTITY, NOT A VALUE. The obvious resolver - scan the registries
for the record whose `est` rounds to the published number - was rejected, and it
is worth saying why, because it looks like it works: it reproduces every figure
today. It is a proxy. A second record carrying the same estimate is
indistinguishable from the right one, so the scan would silently start returning
a different measurement the first time the sweep grew a colliding record.
Recorded in CLAUDE.md's proxy-is-not-the-thing table.

So a `Pointer` names the registry FILE and the `(family, name)` key inside it,
and `resolve()` requires EXACTLY ONE match. `(family, name)` is unique across all
8 registries at 1,171 records; a pointer resolving to 0 or 2 records raises
rather than choosing. THE INPUTS ARE EXPLICIT ON PURPOSE: the resolver reads one
named file, never a glob over `results/*.jsonl`, so a registry gaining records
can change this file only by colliding on a key - which raises - and never by
quietly widening what a scan would match.

INCREMENT 1 OF N. Only R11 is registry-backed today. The other sixteen carry
their literals here unchanged and are converted one script at a time; the ones
that print rather than register need `Registry` adopted first. R14 will be a
computation rather than a lookup (`summarize.grade()` over `candidates.json`), so
it gets a resolver of its own with its inputs named the same way.

ROUNDING IS A DISPLAY DECISION AND STAYS HERE. `digits` is per-pointer: the
registry keeps full precision, the site shows 2dp for a pp figure and 4dp for a
Brier difference. The generator never writes back to a registry.
"""
import argparse
import datetime
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(ROOT, "research", "sweep", "results")
OUT = os.path.join(ROOT, "docs", "hypotheses.json")

# The contract CLOSES this object (`additionalProperties: false` on `Hypothesis`
# in web/contract/v2/contract.schema.json), so these are exactly the keys - no
# more, no fewer. That is why a pointer cannot ride along in the emitted file and
# lives in this module instead.
FIELDS = ("id", "brief", "date", "question", "verdict", "metric", "estimate",
          "interval", "unit", "n", "games", "why", "script")

NOTE = ("The research record published on the site, emitted by jobs/build_hypotheses.py and not hand-edited. A record carrying a registry pointer has its figure read from the producing script's own append-only registry, by (registry, family, name), and rounded for display; the remainder still carry numbers transcribed from CLAUDE.md and are being converted one at a time. Where no interval was computed, the interval is null. verdict: retired (tested, no edge) | null (tested, no effect) | not_testable (data does not exist) | open (awaiting a holdout).")


class Unresolvable(RuntimeError):
    """A pointer that does not name exactly one estimable registry record."""


class Pointer:
    """Where a figure comes from, by identity.

    `registry` is a FILE NAME under research/sweep/results, never a glob - see
    the module docstring on explicit inputs. `digits` is display rounding only.
    """

    __slots__ = ("registry", "family", "name", "digits")

    def __init__(self, registry, family, name, digits):
        self.registry, self.family = registry, family
        self.name, self.digits = name, digits

    def __repr__(self):
        return "Pointer(%s:%s/%s)" % (self.registry, self.family, self.name)


HYPOTHESES = [
    {
        "id": "R01",
        "brief": "longshot",
        "date": "2026-09-12",
        "question": "Do sportsbooks overprice longshot player props?",
        "verdict": "retired",
        "metric": "realized rate in the 0.125-0.150 price bucket (priced 0.1388)",
        "estimate": 0.0898,
        "interval": [0.0552, 0.1429],
        "unit": "probability",
        "n": 167,
        "games": None,
        "why": "Every settled observation priced below 0.15 is a sacks over on a single-book 1-2 line ladder, and even there the priced value sits inside the Wilson interval.",
        "script": "research/longshot.py",
    },
    {
        "id": "R02",
        "brief": "S00",
        "date": "2026-09-14",
        "question": "Does the model's week-1 entry beat the Kalshi close?",
        "verdict": "retired",
        "metric": "executable CLV at 1,000 contracts",
        "estimate": -13.52,
        "interval": [-17.06, -10.4],
        "unit": "pp",
        "n": 493,
        "games": 14,
        "why": "Mid-to-mid CLV was +0.62pp [+0.13, +1.22], but the book tightened from below; where money changes hands CLV is deeply negative.",
        "script": "research/clv.py",
    },
    {
        "id": "R03",
        "brief": "S01",
        "date": "2026-09-14",
        "question": "Held to settlement, does one crossing at the touch pay?",
        "verdict": "retired",
        "metric": "one-crossing CLV against the closing mid",
        "estimate": -3.0,
        "interval": [-3.8, -2.26],
        "unit": "pp",
        "n": None,
        "games": None,
        "why": "Exactly mid-to-mid minus half the 7.22c entry spread; shrinking the ticket buys back slippage and nothing else.",
        "script": "research/clv.py",
    },
    {
        "id": "R04",
        "brief": "M01",
        "date": "2026-09-14",
        "question": "Would a resting maker order on the model's side have earned the spread?",
        "verdict": "retired",
        "metric": "maker CLV conditional on a fill",
        "estimate": 0.28,
        "interval": [-0.34, 1.0],
        "unit": "pp",
        "n": 704,
        "games": 14,
        "why": "Fill rate 18.0%; the fills were worth nothing while the misses were worth everything (selection gap +4.52pp [+3.58, +5.79]).",
        "script": "research/maker.py",
    },
    {
        "id": "R05",
        "brief": "018",
        "date": "2026-09-14",
        "question": "Does the model's side selection add value over a model-free maker control?",
        "verdict": "retired",
        "metric": "conditional maker CLV, model's own side, 100 contracts",
        "estimate": 0.67,
        "interval": [0.05, 1.39],
        "unit": "pp",
        "n": None,
        "games": 14,
        "why": "The flipped side reads +0.07pp [-0.69, +0.93]; the weekly product peaks at ~$113/week at 250 contracts - real but small.",
        "script": "research/maker.py",
    },
    {
        "id": "R06",
        "brief": "019 H1",
        "date": "2026-09-15",
        "question": "Do Kalshi ladder rungs violate monotonicity on the touch, tradeably?",
        "verdict": "retired",
        "metric": "violation episodes surviving taker fees on both legs",
        "estimate": 232,
        "interval": None,
        "unit": "episodes of 892",
        "n": 892,
        "games": None,
        "why": "95% are in-game rungs updating out of step; where depth could be checked the thinner leg carried a median of 1 contract, max 2.",
        "script": "research/structural.py",
    },
    {
        "id": "R07",
        "brief": "019 H2",
        "date": "2026-09-15",
        "question": "Do first-TD-scorer partitions sum to more or less than 1?",
        "verdict": "not_testable",
        "metric": "incomplete first-TD events",
        "estimate": 16,
        "interval": None,
        "unit": "events of 18",
        "n": 18,
        "games": None,
        "why": "13 of 16 settled week-1 events had no No Touchdown leg, so the partition sum is undefined.",
        "script": "research/structural.py",
    },
    {
        "id": "R08",
        "brief": "019 H3",
        "date": "2026-09-15",
        "question": "Does quoting both sides capture the spread, per series?",
        "verdict": "retired",
        "metric": "conditional maker capture, KXNFLSPREAD",
        "estimate": -0.83,
        "interval": [-1.67, -0.29],
        "unit": "pp",
        "n": None,
        "games": None,
        "why": "Negative on every tight series and zero on the prop series; spread and maker fee are collinear, so the ordering cannot separate them.",
        "script": "research/structural.py",
    },
    {
        "id": "R09",
        "brief": "020",
        "date": "2026-09-15",
        "question": "Does Kalshi's team-market price lag the sportsbook consensus by more than it costs to cross?",
        "verdict": "null",
        "metric": "mean gap, Kalshi mid minus de-vigged consensus",
        "estimate": -0.19,
        "interval": [-0.39, 0.01],
        "unit": "pp",
        "n": 1060,
        "games": 16,
        "why": "Pre-kickoff gaps above 2.5pp were three claims on minority half-point lines and CLV to Kalshi's own close was -0.9pp; in-game gaps were book clock skew.",
        "script": "research/consensus.py",
    },
    {
        "id": "R10",
        "brief": "021",
        "date": "2026-09-15",
        "question": "Is the model's probability better than the market's on settled outcomes?",
        "verdict": "retired",
        "metric": "Brier(model) - Brier(market)",
        "estimate": 0.024,
        "interval": [0.0089, 0.0373],
        "unit": "Brier",
        "n": 706,
        "games": 14,
        "why": "Worse than the market, with the interval excluding zero and the estimate above the minimum detectable effect. Restated 2026-09-17 after the settlement fix: the population grew from 682 to 706 as played-with-no-stat-row outcomes began settling at 0, and the model now scores IDENTICALLY to the smoothed prior-season rate (0.1916 against 0.1916) rather than merely close to it.",
        "script": "research/score.py",
    },
    {
        "id": "R11",
        "brief": "022 H1",
        "question": "After a player reaches a receptions rung mid-game, is the winning side still offered?",
        "verdict": "open",
        "metric": "executable net per contract at 120s, 10 contracts, depth-confirmed",
        "why": "Passes four of five bars and awaits week-2 replication; about $17 for the week, and final-stat determination is look-ahead against the live feed.",
        "script": "research/sweep/h1_settlement.py",
        # The number, by identity. Reproduces the published
        # 1.81 [1.24, 2.47] n=16 games=8 from est=1.8125..,
        # lo=1.2375.., hi=2.4706.. rounded to 2dp.
        "figure": Pointer(registry="h1.jsonl", family="H1_net_mean",
                          name="KXNFLREC|yes_midgame|G120|C10", digits=2),
    },
    {
        "id": "R12",
        "brief": "022 H2",
        "date": "2026-09-15",
        "question": "Does order-book imbalance predict a move larger than the spread plus fee?",
        "verdict": "retired",
        "metric": "top-decile move minus half-spread and fee",
        "estimate": None,
        "interval": None,
        "unit": "pp",
        "n": None,
        "games": None,
        "why": "The in-game slope is real and replicates on college football, but net of cost is negative in all 30 NFL and all 16 estimable CFB cells; the largest gross move is ~1.1pp against 1.5-24pp of cost.",
        "script": "research/sweep/h2_imbalance.py",
    },
    {
        "id": "R13",
        "brief": "022 H3",
        "date": "2026-09-15",
        "question": "When is the Kalshi book widest and tightest?",
        "verdict": "null",
        "metric": "spread lifecycle (execution map, not an edge)",
        "estimate": None,
        "interval": None,
        "unit": None,
        "n": None,
        "games": None,
        "why": "Cross game lines 1-6h before kickoff; never cross a prop in-game, where REC spreads reach 16c and RSHATT 63c on 1-2 contracts.",
        "script": "research/sweep/h3_lifecycle.py",
    },
    {
        "id": "R14",
        "brief": "022 scan",
        "date": "2026-09-15",
        "question": "Does an open scan of week 1 find anything that clears five pre-registered bars?",
        "verdict": "retired",
        "metric": "findings among candidates",
        "estimate": 0,
        "interval": None,
        "unit": "findings of 101 candidates",
        "n": 101,
        "games": None,
        "why": "396 search tests, 230 at nominal p<0.05 against 16.4 expected; the replicating effects all lose to cost.",
        "script": "research/sweep/summarize.py",
    },
    {
        "id": "R15",
        "brief": "023 Part 1",
        "date": "2026-09-15",
        "question": "Walk-forward, does the model beat the de-vigged sportsbook close?",
        "verdict": "retired",
        "metric": "Brier(model) - Brier(close), 2025",
        "estimate": 0.0195,
        "interval": [0.0144, 0.0253],
        "unit": "Brier",
        "n": 6031,
        "games": 284,
        "why": "Worse in every season (2023 +0.0229, 2024 +0.0237) against an MDE of ~0.008, and in all 13 variants bracketing the model's unverified constants. Restated 2026-09-17 after the settlement fix, which grew every season's sample and collapsed the separate corrected arm into the primary one - it now reports 0 outcomes moved.",
        "script": "research/walkforward.py",
    },
    {
        "id": "R16",
        "brief": "023 Part 2",
        "date": "2026-09-15",
        "question": "Do sportsbooks disagree enough for arbitrage or middles?",
        "verdict": "retired",
        "metric": "historical receptions arbitrage mean size",
        "estimate": 1.09,
        "interval": None,
        "unit": "pp",
        "n": None,
        "games": None,
        "why": "Arbs appear on 2.53% of receptions opportunities from slow books, fail replication, and are worth ~$5 per $500 instance before limiting; every middle loses.",
        "script": "research/bookvbook.py",
    },
    {
        "id": "R17",
        "brief": "023 Part 3",
        "date": "2026-09-15",
        "question": "Do teammates' lines lag an inactive announcement?",
        "verdict": "not_testable",
        "metric": "inactive events with an announcement timestamp",
        "estimate": 0,
        "interval": None,
        "unit": "events",
        "n": 0,
        "games": None,
        "why": "No source on disk timestamps an inactive; Kalshi pulled a Friday-OUT player's markets on Saturday, removing the game-day window for known outs.",
        "script": "research/inactives.py",
    },
    {
        # Registered BEFORE any call exists, so it has no figure and cannot have
        # one yet. The script is the frozen rule; it exits 2 until a call ledger
        # exists. docs/briefs/f13-fantasy-calls-preregistration.md.
        "id": "R18",
        "brief": "f-13",
        "date": "2026-09-24",
        "question": "Do the fantasy board's buy-low and sell-high calls hold up - does a band's scoring beyond what its volume bought carry into the games after the call?",
        "verdict": "open",
        "metric": "excess carried fraction of the sell-high minus buy-low residual, PPR, next game and rest of season",
        "estimate": None,
        "interval": None,
        "unit": "fraction",
        "n": None,
        "games": None,
        "why": "Pre-registered before the board made a single call: supported if at most a quarter of the residual carries, failed if more than a quarter does, graded once per season against a world with no persistent efficiency. A failed band is published and stays on the page.",
        "script": "research/f13_call_grading.py",
    },
]


def resolve(ptr, results_dir=RESULTS):
    """The one record `ptr` names, or raise.

    Raises on zero matches (the measurement moved or was renamed) and on two or
    more (the key collided, so the pointer no longer identifies anything). It
    never picks one - picking is precisely what makes a value-scan wrong.
    """
    path = os.path.join(results_dir, ptr.registry)
    if not os.path.isfile(path):
        raise Unresolvable("%r: no registry file at %s" % (ptr, path))
    hits = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            rec = json.loads(line)
            if (rec.get("family"), rec.get("name")) == (ptr.family, ptr.name):
                hits.append(rec)
    if len(hits) != 1:
        raise Unresolvable(
            "%r: %d records match in %s, expected exactly 1. A pointer identifies a "
            "measurement; it does not choose between measurements."
            % (ptr, len(hits), ptr.registry))
    rec = hits[0]
    if not rec.get("estimable"):
        raise Unresolvable("%r: the record exists but is not estimable" % (ptr,))
    return rec


def figure(ptr, results_dir=RESULTS):
    """The six fields a registry record supplies.

    `date` is when the interval was COMPUTED - the record's own `ts` - not when
    someone wrote it down. That is the same field bar 1 of the sweep compares
    against the candidates doc's commit time.
    """
    rec = resolve(ptr, results_dir)
    d = ptr.digits
    when = datetime.datetime.fromtimestamp(rec["ts"], datetime.timezone.utc)
    return {"date": when.date().isoformat(),
            "estimate": round(rec["est"], d),
            "interval": [round(rec["lo"], d), round(rec["hi"], d)],
            "unit": rec.get("unit"),
            "n": rec.get("n"),
            "games": rec.get("games")}


def build(results_dir=RESULTS):
    out = []
    for spec in HYPOTHESES:
        rec = dict(spec)
        ptr = rec.pop("figure", None)
        if ptr is not None:
            rec.update(figure(ptr, results_dir))
        missing = [k for k in FIELDS if k not in rec]
        if missing:
            raise Unresolvable("%s: missing %s" % (rec.get("id"), missing))
        extra = [k for k in rec if k not in FIELDS]
        if extra:
            raise Unresolvable(
                "%s: %s is not in the contract, which closes this object"
                % (rec.get("id"), extra))
        out.append({k: rec[k] for k in FIELDS})
    return {"note": NOTE, "hypotheses": out}


def render(doc):
    """LF and a trailing newline. `.gitattributes` normalises on the way in, so
    the working tree may hold CRLF; --check compares normalised text."""
    return json.dumps(doc, indent=2, ensure_ascii=True) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="emit docs/hypotheses.json")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if the file on disk is not what the generator emits")
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args(argv)

    doc = build()
    text = render(doc)
    pointered = sum(1 for h in HYPOTHESES if "figure" in h)

    if args.check:
        try:
            with open(args.out, encoding="utf-8", newline="") as f:
                have = f.read().replace("\r\n", "\n")
        except OSError as exc:
            print("cannot read %s: %s" % (args.out, exc))
            return 1
        if have != text:
            print("DRIFT: %s is not what the generator emits. "
                  "Run `python -m jobs.build_hypotheses`." % args.out)
            return 1
        print("ok: %s matches the generator (%d records, %d registry-backed)"
              % (args.out, len(doc["hypotheses"]), pointered))
        return 0

    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print("wrote %s: %d records, %d registry-backed, %d still literal"
          % (args.out, len(doc["hypotheses"]), pointered,
             len(doc["hypotheses"]) - pointered))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
