# f-36 — what `tree_census.py`'s exit status reports, and the figures f-34 listed

Stacked on `f-35-reliability-run5` @ ee7a9c8. Read-only against every store; trees are git
archives and copies under `D:/temp/f36`, never the live export. The rule is
`PREREGISTRATION.md`, committed (665e4c5) before any script changed. Logs of the one run are
in `runs/2026-10-08-f36/`.

    PY=D:/calibrated-sports/venv-f/Scripts/python.exe
    $PY research/f30_cfb_build/tree_census.py --src <target worktree> --nfl <NFL tree> --cfb <staged tree> \
        --baseline <baseline.json> --out <json>                                  # ~10 min alone, ~30 min seven at once
    $PY research/f36_exit_status/doctor.py --src <a-78 worktree> --src-opaque <a-72 worktree> --work <empty dir>   # ~3 min

## Verdict: `exit status repaired`

Exit 0 from `tree_census.py` now means all of: step 1 reproduced the baseline; every missing
or undeclared path the walker or the tool's full run reports is named in the baseline's
`known`; the two differ on no path `known` does not name; `known` names nothing unfound.
Anything else exits 1 with one `EXIT 1:` line per reason, and the decision is written into
`--out` under `exit` before the process leaves.

A finding is acknowledged only by `{"list", "path", "source"}` in the baseline: exact path,
its own list, a source. No flag, no wildcard, no default.

### Real trees

| run | exit | what printed |
|---|---|---|
| a-78's tree, f-34's `baseline-a78.json` unchanged | **0** | 0 MISSING, agreement True on both kinds |
| a-72's tree, f-34's `baseline-a72.json` unchanged | **1** | five `MISSING`, one tool-only `undeclared`, one disagreement line: 7 reasons |
| a-72's tree, `baseline-a72-known.json` (names all six, with sources) | **0** | the same five MISSING lines and `False`, then six `acknowledged by the baseline` lines |
| a-72's tree, `baseline-a72-known-4of5.json` | **1** | names `...ids.yahoo` twice: as MISSING and in the disagreement |
| a-78's tree, a baseline whose `known` names a finding the tree does not have | **1** | "'known' names missing ...ids.gsis ... and neither the census nor the tool reports it" |

All five are as predicted in the pre-registration. The second row is the one to read
carefully: **a-72's tree goes red on the baseline f-34 committed for it.** That is not a
declared absence turning red. a-72's manifest has 34 `absences` entries and none covers
`player_summary.identity.ids`; the five keys were *reported* by f-30, not declared by the
tree. On a-78's tree, where they are declared, the same walker prints `59 beneath a declared
absence + 0 MISSING` and the status is 0. The brief's phrase "declared, known absence" joins
two different things; only "known" is true of a-72's tree.

### Doctored trees (`doctor.py`, 14 cases, each a separate process)

| case | doctoring | exit | reason printed |
|---|---|---|---|
| `clean` | none; three declared absences in the tree | 0 | `3 beneath a declared absence + 0 MISSING` |
| `missing` | a first-sport key college lacks, undeclared | 1 | census reports MISSING |
| `missing-known` | the same, named in `known` | 0 | MISSING still printed, then acknowledged |
| `missing-wrong-list` | the same, named under `undeclared` | 1 | MISSING, and the entry that excuses nothing |
| `undeclared` | a college key empty in every file, undeclared | 1 | census reports UNDECLARED |
| `team-undeclared` | the same in a `team` file, a kind the walker does not read | 1 | the tool's full run reports undeclared |
| `disagree` | ids differ under a tool that treats the map as opaque (a-72's) | 1 | MISSING, and the disagreement |
| `disagree-known` | the same, named | 0 | `False` printed, acknowledged |
| `step1` | one baseline total moved | 1 | step 1 DOES NOT REPRODUCE |
| `stale-known` | clean tree, `known` names a finding | 1 | the entry that excuses nothing |
| `known-no-source` | a `known` entry without a source | 1 | refused at load, no output written |
| `stale-count` | a player directory deleted after the baseline | 1 | `<- DIFFERS`, step 1 |
| `stale-one-file` | one summary rewritten after the baseline | **0** | nothing |
| `stale-every-value` | every stat value in every college file rewritten | **0** | nothing |

14 of 14 as registered; in every case that wrote an output, `exit.code` in the file equals
the process's status.

**A disagreement can never be the only reason.** Every path the walker and the tool differ on
is a finding of exactly one of them, so it is either unacknowledged (and already fails as a
finding) or named. Reason 5 of the pre-registration is therefore information for the reader
and not an independent gate; a test asserts it. Reason 7 (the written flag contradicting the
written lists) is reachable only by editing a result by hand, which is how it is tested.

## The stale manifest

A tree whose player files change while its manifest does not keeps its stamp, so the
baseline is accepted. What happens next depends on what changed:

- **The file count changed** (one player's four files removed from a copy of a-78's tree):
  `cfb player_summary: 14,309 files (baseline 14,310) <- DIFFERS`, step 1 DOES NOT REPRODUCE,
  exit 1. Loud.
- **Only content changed** (1,000 of 14,310 summaries rewritten in a copy - every name, every
  non-zero career stat +1000 - manifest byte-identical): `trees: ... (match)`, step 1
  REPRODUCES, 0 MISSING, agreement True, **exit 0**. Silent. The synthetic `stale-every-value`
  case rewrites every stat in every file and is also exit 0.

This is the tool's design, not a bug in the stamp check: every total it compares is about
which *paths* exist and whether they are empty everywhere, so no change to a value can move
one. The consequence is that a baseline certifies a tree's shape and says nothing about
whether it is the same tree. **Proposed, not built:** the baseline's `trees` carry, beside
`generated_at`, a digest over the sorted `(relative path, sha256)` of every file under the
sport prefix, and `require_same_trees` refuses on either.

## The six figures f-34 listed, each with something moved under it

f-34 established these by reading. Here a tree, a store copy or a doctored target was moved.

| figure | moved | result |
|---|---|---|
| `slug_evidence.py:26` `len(players) > 10000` | the staged index cut to 9,999 players, and to 10,001 | 9,999: **stops** - `AssertionError: 9999`, exit 1, a five-line traceback and no report line. 10,001: runs, 15 lines, exit 0. It cannot change a verdict word. It can replace the whole report with a traceback. |
| `slug_evidence.py:9` "the 14,327" (docstring) | a-78's tree in place of a-72's | prints `staged index: 14310 players`; "14,327" appears nowhere in the output. Cannot change anything; it is stale prose on a-78's tree. |
| `identity.py:28` `len(res["rows"]) > 1000` | a stub target whose season model returns 1,000 rows, and 1,001 | 1,000: **stops** - `AssertionError: 1000`, exit 1, no hash printed. 1,001: prints the hash, exit 0. The real store was not moved (f-30 read 3,304 rows). |
| `part1_c39.py:5` "9,600-point grid" (docstring) | a copy of c-39's tree with the comparator's `k` list halved | the target's grid is 9,600 points as committed and 4,800 in the copy. The script printed `1 of 21 seasons equal the committed fit`, `vs elo_nomov ... DOES NOT REPRODUCE`, exit 1 - from the target's grid, which it reads. The docstring still says 9,600. Cannot change anything. |
| `attack_c39_prices.py:7` "n 9,652" (docstring) | a copy of `cfb.db` with season 2013's 2,279 line rows removed | the script printed `n 8921 (published 9652)`, reading the published figure from the target's file. The docstring figure is not read. Cannot change anything. |
| `attack_c39_prices.py:147` "(c-39: +0.668 on 9,822)" (printed) | the same store copy | printed `corr ... +0.662 on 9085 games (c-39: +0.668 on 9,822)`. It is compared with nothing and changed no word and no status. It does print c-39's figure beside a number it no longer describes, with nothing marking it as a fixed string. |

So f-34's reading holds for the four prose figures and is incomplete for the two asserts:
neither can change a verdict word or turn a failure into exit 0, and both can stop the script
before it prints anything.

**Found while moving the store, and outside the six:** on that store copy
`attack_c39_prices.py` printed `DOES NOT REPRODUCE` on all three 2b lines and **exited 0**.
Its only failing exit is the 2a premise check. That is the defect f-34 fixed in
`tree_census.py` step 1, in a sibling. Not changed here; reported.

## The widened test

`tests/test_f30_no_hardcoded_totals.py` now walks the four gaps f-34 wrote down:

| gap | shape | planted and seen to fire |
|---|---|---|
| a number under 1,000 in a comparison | `equal` (`==`, `!=`, `in` against anything but 0) and `tolerance` (a number subtracted inside a comparison) | `totals['declared'] == 21`, `got in (85, 80, 8)`, `abs(est - 0.0321) < 5e-5` |
| a bare call argument | `argument` (1,000 or more, positional or keyword) | `check_files(rep, 14310)`, `check_files(rep, expect=40543)` |
| a figure built from parts | literal arithmetic and joined literal strings are folded first | `got == 14000 + 310`, `14 * 1000 + 310`, `10 ** 9`, `"14," + "310"`, `f"{14},{310}"` |
| a string without a thousands comma | `bare-text` (four or more digits standing alone) | `"a-78 stated 14310 pages"`, a docstring |

**New hits across `research/f30_cfb_build/`: 7, at 6 distinct (file, shape, figure) keys.**
Five are harmless and named with a reason. One is not:

- **`part1_c39.py:45`, `draws=2000`.** c-39's bootstrap draws, kept as a second copy of the
  target's own default (`research.ranking_calibration.BOOT`, 2000 at a981315). If the target's
  default moves and it republishes, the bounds stop matching and this script prints DOES NOT
  REPRODUCE on a claim that holds. It is allowed by name with that sentence, because the unit
  bars changing this file's numerical behaviour. I did not exercise it.

A first draft of the walk fired 45 times (every `round(x, 4)`, `range(20)`, `timeout=5` and
`p < 0.05`); a guard that is all noise on its first run gets switched off, so the shapes were
narrowed to where a total is *tested for* before anything was allowed. What that costs is in
the test's docstring: an ordering comparison against a number under 1,000 (`assert n > 85`),
equality against 0, an argument under 1,000, a figure assigned to a name and compared through
the name, and a count under 1,000 in a string are still not seen.

## Not run

- `identity.py` against the real NFL store (about 7 minutes holding a read on the live WAL).
  The floor was exercised on a stub target, not by moving the store.
- The `draws=2000` hazard: read, not exercised.
- The content digest: proposed, not built. A one-off measurement of it (sha256 of every file under `cfb/`, 54,993
  files) gave `c0885781...` for a-78's tree and `7119c3dd...` for the rewritten copy, so it would tell the two
  apart; it took 399 s and 742 s with a cold cache while the test suite was running, which is the census's own
  order of cost and was not measured on a quiet machine.
- `part1_c39.py`, `identity.py` and `identity_c39.py` are unchanged; `part1_c39.py` was run
  once, against the doctored copy only.
