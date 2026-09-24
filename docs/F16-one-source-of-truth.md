# F16 — which number is current, why R11 cannot be re-graded, and player-page freshness

Unit f-16, 2026-09-24. Read-only: `market_log.db` opened `mode=ro`, the export tree
read, the live site fetched over HTTP. Re-derive with:

    python -m research.score                                   # R10
    python -m research.walkforward --workers 12                # R15, all seasons
    python -m research.f16_sources_of_truth --populations --h1-inputs \
        --freshness --export-dir <WEB_EXPORT_DIR>

## 1. One table

| figure | value | interval | n | games | population | script | quote? |
|---|---|---|---|---|---|---|---|
| R10 model − market Brier (site) | +0.0240 | [+0.0089, +0.0373] | 706 | 14 | 2026 wk1 Kalshi REC+RSHATT predictions, settled, two-sided at entry; post-settlement-fix | `research/score.py` | **yes — current, re-run 2026-09-24, exact** |
| R10 as in `model-scoring-verdict.md` | +0.0275 | [+0.0117, +0.0410] | 682 | 14 | same, PRE-fix: played-no-stat-row outcomes dropped | `research/score.py` at `e8064b5` | no — stale, superseded 2026-09-17 (`aecdd95`) |
| R15 walk-forward vs book close (site) | +0.0195 | [+0.0144, +0.0253] | 6,031 | 284 | **2025 season only**, receptions + rush attempts, over side, `p_bench` common set; post-fix | `research/walkforward.py` | **yes — current** |
| R15 per season, current | 2023 +0.0229 / 2024 +0.0237 / 2025 +0.0195 | see §2 | 4,785 / 5,225 / 6,031 | 283 / 285 / 284 | as above, each season | `research/walkforward.py` | yes, per season |
| "14,857 predictions over 813 games" (`three-gaps-closed-and-settlement-bug.md`) | — | none | 14,857 | 813 | SUM of the three PRE-fix seasons (4,441+4,842+5,574; 270+272+271) as first published at `06d26eb` | `research/walkforward.py` at `06d26eb` | no — stale AND pooled; no pooled interval was ever computed |
| pooled current (arithmetic only) | — | none | 16,041 | 852 | sum of the three current seasons | — | only as a count; never beside a single interval |

**What changed R15's population: the settlement fix, not a market or season filter.**
The site's R15 was always one season (its metric reads "…, 2025"); the doc's figure is
three seasons summed. Within 2025, n moved 5,574 → 6,031 (+8.2%), which is the "~7% of
outcomes" `06d26eb` said the played-no-row correction would add, and the
first-published CORRECTED arm for 2025 was already +0.0195 — identical to today's
primary. The game count rose by exactly 13 in each season (270→283, 272→285,
271→284); that part is **not attributed** here.

## 2. R15 re-run, 2026-09-24

Full run, 2,913 s, exit 0, 60 of 60 intervals estimable. Reproduces the site and
CLAUDE.md to the fourth decimal:

    season  n      games  Brier model / close / naive   model - close (p_bench)      vs p_all (secondary)
    2023    4,785  283    0.2695 / 0.2466 / 0.2777      +0.0229 [+0.0170, +0.0290]  +0.0190, n 9,425
    2024    5,225  285    0.2687 / 0.2450 / 0.2833      +0.0237 [+0.0188, +0.0288]  +0.0213, n 9,252
    2025    6,031  284    0.2648 / 0.2453 / 0.2844      +0.0195 [+0.0144, +0.0253]  +0.0204, n 6,693

13 of 13 variants "model worse*" in every season; the corrected arm moves 0 outcomes.
Note the secondary close (`p_all`, any book) is a THIRD population with its own n — a
reader quoting "R15 n" must name which close.

## 3. R11 cannot be re-graded on week 2, and re-running the holdout now would spend it

R11's registered figure is `H1_net_mean KXNFLREC|yes_midgame|G120|C10`: a mean over
**depth-confirmed** observations (a `market_depth` snapshot within 60 s). Week 2's
inputs, measured:

- final scores: 16 of 16 games. Play-by-play: 16 games, 2,733 plays. Markets: 1,118
  REC and 148 RSHATT listed for 26SEP20, all mapped. Quotes: present.
- **settled-market archive**: `kalshi_settled_022` is ONE shard, day 2026-09-15 (the
  week-1 fetch), state `remote`, deleted locally. No week-2 settlement fetch exists and
  no committed script makes one; creating it writes the raw archive and `raw_shards`
  in `market_log.db`.
- **order-book depth for week-2 Sunday props does not exist.** KXNFLREC 26SEP20: 24
  rows on 12 of 1,118 markets, all at one instant, 09-22 13:00Z — after the games.
  KXNFLRSHATT 26SEP20: 0. No REC/RSHATT 26SEP21 depth at all. Week-1 Sunday for
  comparison: 2,476,580 REC rows on 612 markets. Prop depth stops at 09-18 04:14Z
  (after TNF) and resumes 09-24 00:16Z. KXNFLSPREAD 26SEP20 has 1,187,274 rows from
  09-19 17:00Z — exactly 24 h before the 17:00Z kickoffs, the `game` tier boundary — so
  game lines entered the depth tier and props did not. Cause not established.

Depth cannot be recovered after the fact. So the week-2 replication can populate H1's
net figure from TNF (one game) and at most the 12 late-snapshotted markets: under the
pre-registered `MIN_GAMES_TO_READ = 5` it enters BH at p = 1 and is not read. The
holdout is run once (`replicate_week2.py`), so running it now spends it on an
unreadable result.

Proposed R11 `why` (for track A, who owns `jobs/build_hypotheses.py`): *"Passes four
of five bars. The week-2 replication cannot be graded: the logger captured no
order-book depth for week-2 Sunday or Monday player props, and the figure is defined
on depth-confirmed prices. Awaits a week with prop depth."*

## 4. Freshness

Player pages ARE regenerated by the weekly refresh. 421 players have both a week-2
stat row and a 2026 season file; **0** lack week 2. The date a page shows is the file's
`generated_at`, which `write_if_changed` preserves when content is unchanged — it is
"content last changed", not "last checked". Amon-Ra St. Brown's file (09-18) already
carries week 2 (DET at BUF, TNF 09-17); DET had not played since. The 61 files stamped
09-18 are 30 DET/BUF plus players whose last game was week 1. Four players (eight
files) fetched over HTTP are byte-identical to the local export. The register's
"2026-09-17" is the same mechanism: `research/*.json` content last changed then.
