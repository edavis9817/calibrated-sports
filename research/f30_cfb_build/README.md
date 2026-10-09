# f-30 — the college build, attacked before a URL is minted

Adversarial and read-only. Targets: a-72 (the export, `origin/a-72-cfb-on-nfl-contract`
@ b60fdf9, staged tree `D:/temp/a72/new` generated 2026-10-08T07:28:10Z), c-39 (the model,
`origin/c-39-cfb-game-model` @ a981315) and b-103 (the pages, not rendered here by
instruction). Stacked on `f-29-reliability-run3` because `duplication_through()` and
`f26lib` are not on `origin/main`. Logs of the one run are in `runs/2026-10-08-f30/`.

    PY=D:/calibrated-sports/venv-f/Scripts/python.exe
    git worktree add --detach D:/temp/f30/a72src  origin/a-72-cfb-on-nfl-contract
    git worktree add --detach D:/temp/f30/c39src  origin/c-39-cfb-game-model
    git worktree add --detach D:/temp/f30/c39base e8ff867

    $PY research/f30_cfb_build/identity.py D:/temp/f30/c39src --store <NFL store> --diff e8ff867 a981315   # ~7 min
    $PY research/f30_cfb_build/identity.py D:/temp/f30/c39base --store <NFL store>
    $PY research/f30_cfb_build/identity.py D:/temp/f30/c39src --store <NFL store> --plant
    $PY research/f30_cfb_build/part1_c39.py         --src D:/temp/f30/c39src --db <cfb.db>
    $PY research/f30_cfb_build/attack_c39_prices.py --src D:/temp/f30/c39src --db <cfb.db> --out <json>
    $PY research/f30_cfb_build/tree_census.py --src <target worktree> --nfl <NFL tree> --cfb <staged tree> --baseline <baseline.json> --out <json>   # ~11 min; --baseline has no default (f-34)
    $PY research/f30_cfb_build/slug_evidence.py --src D:/temp/f30/a72src --db <cfb.db> --cfb <staged tree>

Run as scripts, never `-m`: `research` must resolve to the TARGET's worktree, and each file
asserts that it did. `cfb.db` is opened `mode=ro`; `market_log.db` only through the target's
own `jobs.season_model.market_log_ro`.

## The pre-registered verdict rule, and what it returned

Publishable only if (1) c-39's settlement result reproduces with its intervals, (2) the hash
claim holds, (3) a-72's conformance claim survives the full-tree key count.

| | result | log |
|---|---|---|
| 1 | three arms, n 15,508, estimate and both bounds equal to the published ones (max abs diff 0.0) through `GF.boot_many` at c-39's draws and seed | `part1.log` |
| 2 | `12b91365...` on a981315 and on e8ff867; a planted `MOV_A + 0.1` gives `49e11b8b...` | `identity.log` |
| 3 | a-72's tool with the sample lifted: missing 0, undeclared 1, declared 21, cfb_only 15 - the sampled totals, over all 78,220 player files | `census.log` |

**All three hold, so the rule returns PUBLISHABLE.** Three things the rule did not ask about
were found on the way and bear on the publish; they are at the end, and none of them is a
condition of the rule.

## What each claim is worth

- **c-39 hash: true, and could not have failed.** The hashed run imports 9 files of the tree
  (read from `sys.modules`, not a hand list); c-39 changes 11 files; the intersection is
  empty. It is evidence that c-39 touched nothing, not that a port left the NFL model intact.
- **c-39 settlement: citable.** Reproduced with its intervals here; f-29 ran the leakage
  audit with plants, coarser blocks and k = 1000 on the same day.
- **c-39 2c, +0.0321 on 168 games: citable only with a sentence.** Reproduces exactly; honours
  blocks through `GF.boot_many`; excludes zero under kickoff-hour, kickoff-day and
  home-conference blocks; survives c-39's own 32 intervals (adjusted p 0.0102) and does not
  survive k = 1000. It is 1.29x its MDE (0.0250) against a "clear" threshold of 1.25, and its
  three weeks are three blocks, which is fewer than can be read. The sentence: *"168 games in
  weeks 3-5 of one season; the estimate is 1.29 times the smallest gap this sample could
  detect, so its sign is established and its size is not."*
- **c-39 2b, the model's side covers 0.4980: citable as a powered null.** Reproduces at all
  three edge thresholds; break-even at -110 (0.5238) is 5.1 SE above the estimate; under 13
  season blocks the hit rate minus 0.5 reads -0.0020 [-0.0086, +0.0042].
- **The comparator.** No text c-39 committed calls the CFBD moneyline a close; every mention
  carries "not a timestamped close". The registered verdict "the method is the limit" is
  nonetheless conditioned on 2a, the untimestamped value. On the 165 games of 2026 weeks 3-5
  that carry both it and a timestamped pre-kickoff quote, it behaves as a pre-kickoff price:
  median absolute difference 0.0071, maximum 0.0657, mean move toward the result
  -0.0011 [-0.0035, +0.0013], and the planted contamination fires. That is evidence about
  2026's weekly read, not about the 2021-25 backfill 2a is scored on.
- **a-72 conformance: citable, and the sampling caveat is closed.** The least-carried NFL path
  is in 379 of 4,002 summaries (9.5%) and 723 of 19,328 season files (3.7%); nothing sits
  near 1 in 400. The plant shows the sampled tool does miss a 1-in-2,000 key (16 of 20 seeds).
- **a-72's "86 to 0" excludes one map by construction.** `player_summary.identity.ids` is in
  the tool's `OPAQUE` set, so its keys are never compared: the NFL carries `gsis`, `pfr`,
  `pff`, `sleeper`, `yahoo` there and college carries `espn` only, with no `absences` entry.
- **b-103: not attacked.** Not rendered here by instruction. Its unphotographed cases stand.

## Outside the rule, and Ethan's to weigh

- **17 of the 14,327 staged player pages are not players.** Their name is `- Team` or ` Team`
  with a positive ESPN id (`team-4892588` and sixteen more), the feed's team-total row. The
  scope filter excludes ids at or below zero only.
- **The id-suffixed slug is not stable without a registry.** The name half is the newest
  roster season's spelling. 85 scoped players (0.59%) slugify differently across their own
  seasons, 80 of them between the newest season and the one before, and 8 roster rows were
  respelled in place across ingests. `aj-green-4596437` a season ago is `aj-green-jr-4596437`
  in the staged tree.
- **Bare names would collide for 503 players (3.5%)** across 229 names.

## f-34 — the rule for a baseline, written before any script was changed

a-78 ran `tree_census.py` unmodified on its restaged tree: exit 0, 0 MISSING, agreement True
on both kinds, and step 1 printed DOES NOT REPRODUCE, because the script carried a-72's
totals and file counts as literals. The rule below was committed before the repair.

**A comparison stays only if it can still fail for the right reason.** A reproduction check
against a baseline supplied at run time can fail correctly, so it stays and takes an
argument. A reproduction check whose baseline is a figure from one superseded export can only
fail because the tree moved; that is not a reproduction check and it is dropped, with one
line here saying what was dropped and why.

Applied, before measuring anything:

- **The baseline is an argument with no default.** A script that compares against a published
  figure takes that figure from a file named on the command line and refuses to start without
  it. A default would be this defect with a newer number in it.
- **A baseline names the tree it describes** (each tree's `manifest.json` `generated_at`). A
  baseline for another tree is refused before anything is compared, with a message that says
  so. It is never reported as a failed reproduction.
- **A failed reproduction is a failed run.** Step 1 printing DOES NOT REPRODUCE while the
  process exits 0 is how a-78's run read as clean; it exits non-zero.
- **Where the target's own committed file already carries the figure, the script reads it
  there** and keeps no second copy.

For the sibling audit, "can produce a false verdict" means: a comparison against the literal
changes a printed verdict word or the exit status when the tree, the store or the target
moves while the claim under test still holds. Those are fixed. A figure in a docstring, a
printed annotation that is compared with nothing, and a sanity floor whose failure is an
`AssertionError` are listed and left alone.

### What f-34 changed, by that rule

- **`tree_census.py` step 1 stays and takes `--baseline`.** Supplied at run time for the tree
  on disk it can fail for the right reason, so it is kept. The literals `PUBLISHED` and
  `EXPECT_FILES` are gone; the seed and sample come from the baseline too. Without
  `--baseline` the script exits 2 before reading anything. A baseline whose `trees` stamps
  are not the two manifests' `generated_at` is refused with "describes another tree".
  A step 1 that does not reproduce now exits 1 after writing its output.
- **Dropped: `PUB["hit0"]` in `attack_c39_prices.py`**, a third copy of c-39's 2b figure that
  no line read.
- **`attack_c39_prices.py` reads c-39's 2a and 2c figures from the target's committed
  `research/results/cfb_game_forecast.json`**, which it already loaded, instead of `PUB`.
  The premise check raised "does not reproduce" against a981315's figures whatever the
  target's own file said.
- The baseline file's shape is in `tree_census.py`'s docstring. Baselines used in a run are
  kept with that run's logs, never beside the scripts.
- `tests/test_f30_no_hardcoded_totals.py` walks every `.py` here for a numeric dict literal,
  a comparison against a number of 1,000 or more, and a string spelling a count with a
  thousands comma. What it allows is named there with a reason.

Listed and left alone (none can change a verdict word or an exit status):

| file | line | figure | what it is |
|---|---|---|---|
| `slug_evidence.py` | 26 | `len(players) > 10000` | sanity floor; fails as an `AssertionError` if the scope ever shrinks below it |
| `slug_evidence.py` | 9 | "the 14,327" | docstring prose, a-72's page count |
| `identity.py` | 28 | `len(res["rows"]) > 1000` | sanity floor on the NFL season-model rows |
| `part1_c39.py` | 5 | "9,600-point grid" | docstring; its comparisons read the target's own result file |
| `attack_c39_prices.py` | 7 | "n 9,652" | docstring prose |
| `attack_c39_prices.py` | 147 | "(c-39: +0.668 on 9,822)" | printed beside the sign check, compared with nothing |
| `tree_census.py` | plant | 2,000 files, file 1234, seeds 0-19, SAMPLE 400, seed 72 | the synthetic plant's own parameters |
| `../f26_reliability/identity_c39.py` | - | none | carries no figure |

The f-34 run is in `runs/2026-10-08-f34/`, with the two baselines it used.

## f-36 — the exit status, and the listed figures exercised

`tree_census.py` no longer exits 0 beside a printed finding. Exit 0 means step 1 reproduced
AND every missing or undeclared path (the walker's, and the tool's full run over all five
page kinds) is named in the baseline's optional `known` list, by exact path, under its own
list, with a source; `known` naming something that was not found is also exit 1. The rule,
the runs and the verdict (`exit status repaired`) are in `../f36_exit_status/`.

- **a-72's tree exits 1 on `runs/2026-10-08-f34/baseline-a72.json`**, which names nothing. Use
  `../f36_exit_status/runs/2026-10-08-f36/baseline-a72-known.json`, which names the five id
  keys (source f-30) and `sport_manifest.unresolved_ids[].name` (source a-72's own total).
  a-78's tree exits 0 on its f-34 baseline, unchanged.
- **Exit 0 still does not mean** no `FILLED-BUT-DECLARED` line (seven on both trees) and no
  blind-zone path, and it does not mean the tree's values are the ones the baseline's author
  saw: a tree rewritten under an unchanged manifest reproduces (f-36, the stale manifest).
- **The table above says the listed figures cannot change a verdict word or an exit status.
  That holds. Two of them can stop the script:** `slug_evidence.py:26` on an index of 9,999
  players and `identity.py:28` on 1,000 season-model rows each end in an `AssertionError`
  with no report line.
- **`attack_c39_prices.py` exits 0 while printing DOES NOT REPRODUCE on 2b** (seen on a store
  copy with one season's lines removed). Only its 2a premise check fails the process. Not
  changed by f-36.
- **`part1_c39.py:45` `draws=2000`** is a second copy of the target's
  `research.ranking_calibration.BOOT`. Found by the widened test, allowed there by name with
  the reason, not changed.
