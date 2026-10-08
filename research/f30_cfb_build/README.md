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
    $PY research/f30_cfb_build/tree_census.py --src D:/temp/f30/a72src --nfl <NFL tree> --cfb <staged tree> --out <json>   # ~11 min
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
