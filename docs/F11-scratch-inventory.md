# F11 — the working space units leave behind (unit f-09)

Read-only inventory, 2026-09-22 ~22:40 EDT. **Nothing was deleted, moved or re-pointed.**
Re-derive with:

    py -3.12 research/f09_scratch_inventory.py --out D:/temp/f09/inventory.json --markdown D:/temp/f09/table.md

The script walks every directory beside the four repositories and every unit- or tool-named
directory under `D:\temp`. It never follows a reparse point; it records the link and its target
instead. It runs git with `--no-optional-locks`, so inspecting a tree does not rewrite that tree's
index. It decides "is this pushed" by asking a freshly fetched clone of the GitHub repository, not
the tree's own `origin`. For a `git clone <local path>` scratch clone, that `origin` is a local repo.

## 1. The dangerous check: does any scheduled task point into scratch?

**No.** Every task that runs this project's code launches from one of the main working trees:

| task | runs | from |
|---|---|---|
| CalibratedSports Logger (boot) / (logon) | `start_logger.ps1` (resolves its own dir) | `code\calibrated-sports` |
| CalibratedSports Weekly Refresh | `.venv\Scripts\python.exe -m jobs.weekly_refresh` | `code\calibrated-sports` |
| CalibratedSports CFB Odds Forward (every 5 min) | `.venv\Scripts\pythonw.exe -m jobs.ingest_cfb --odds-forward --log` | `code\cs-cfb` |
| CalibratedSports CFB Odds P1 (one-shot, ran 09-19) | `run_cfb_job.cmd` (`cd /d %~dp0`) | `code\cs-cfb` |
| CalibratedSports CFB Weekly | `run_weekly_cfb.cmd` (hard-coded `cd /d ...\cs-cfb`) | `code\cs-cfb` |
| CalibratedSports Relay Resume (hourly) | `start-night.ps1 -Quiet` | `code\_relay` |
| KillCfbProbe | `D:\calibrated-sports\kill_cfb_probe.ps1` | outside every repo |

`run_cfb_job.cmd` is location-relative, so a copy in a stray tree *would* run that tree's code if
something invoked it there. No task does, and no stray tree has a `.venv` for it to find. The
relay (`run/relay.ps1`, `$RepoMap`) also launches every track in its main tree.

**What the check did find is the a-10 problem, live.** The tasks run whatever those two working
trees have checked out.

- `cs-cfb` is on local `main` at `ba81999`, **11 commits behind `origin/main`**. It is an
  ancestor, so it has not diverged; it has just not been advanced. The CFB tasks run that code.
- `cs-cfb\run_weekly_cfb.cmd` carries an **uncommitted modification** (mtime 2026-09-22 00:45).
  It drops `--log` and appends to `D:\calibrated-sports\data\cfb\logs\weekly_cfb.log` instead of
  the committed `<STORAGE_DIR>\cfb\logs\ingest_cfb.log`. The 09-22 09:00 weekly run used the
  uncommitted version (last result 0). This is track C's to resolve, and a-10's production clone
  makes it moot.
- `code\prod\` (created 22:34 tonight) is **a-10's production clone, in build**. It is not scratch.

## 2. What the brief got wrong

- **They are not clones.** `cs-analytics-f06/-f07/-f08` and `calibratedsports-web-b03/-b07` are
  **registered git worktrees**: `.git` is a file pointing into the main clone. They are removed
  with `git worktree remove`. `rm` would leave a stale registration behind.
- **They carry no `.venv`, and the two web worktrees carry no `node_modules` of their own.**
  Their `node_modules` is an **NTFS junction into `code\calibratedsports-web\node_modules`**. That
  is the shape that let b-09's `--force` delete a real `node_modules`. The code-side strays total
  **0.28 GB**, not the multi-venv footprint the brief implies.
- **There are more than two.** Beside the repos there are five worktrees (f06, f07, f08, b03,
  b07). Under `D:\temp` there are 13 more registered worktrees (two nested inside `f03` and
  `f04`) and 11 standalone scratch clones.
- **`D:\temp\a01` no longer exists.** It had already been removed when this ran.
- **`D:\temp\miniflare-*` is not large right now.** There are 42 directories, 3 files each,
  1.9 MB in total. The ~10 GB/day figure did not reproduce at this instant. b-16 owns the
  measurement, and a snapshot cannot show what the leak looks like while a build is running.
- **The real risk is not disk, it is unpushed work.** Five units' commits existed on no remote
  branch: f-03, f-04 and f-05 (track F), and c-04 and c-06 (track C). A `git worktree remove`
  keeps the branch. But a clean-up that also prunes branches, or a fresh clone, would have lost
  them. This unit pushed f-03, f-04 and f-05. c-04 and c-06 still exist only on disk.

## 3. Totals

Under inspection: **164 directories, 7.66 GB**, of which 0.89 GB is a-10's production clone and is
not scratch.

**Reclaimable today on the evidence: 5.79 GB across 112 directories** (the REMOVE rows). A
further 0.76 GB sits in LIVE? rows, which should become reclaimable once their units are idle.
This figure goes stale by the hour: units are writing new scratch as it is read.

With 416 GB free on D:, this is housekeeping, not an emergency. C: has 59 GB free (94% used) and is
the drive that matters. It holds only the 0.28 GB of stray worktrees.

Verdict key:
- **LIVE**: its unit is running now.
- **LIVE?**: written in the last hour (ignoring `.git`). Recheck in daylight.
- **KEEP**: holds commits on no remote branch.
- **LOOK**: has uncommitted changes.
- **REMOVE**: pushed, clean and idle. The column says how.

The table below was generated **after** this unit pushed f-03, f-04 and f-05. The first run had
them as KEEP (5 KEEP rows, 5.00 GB REMOVE); pushing moved them to REMOVE.

Notes on the LOOK rows, from diffing their files against the commits that landed:
- `c01_clone`: its three untracked files are byte-identical to `f6a5fb2` (c-01, on `main`). Safe.
- `f07_clone`: `analytics/drafting.py` is identical to `17b99c0` (F07, on `main`). Two test files
  differ. They are most likely an earlier draft, but that is not established.
- `f02_clone`: `analytics/drafting.py` and `docs/track-a-requests.md` differ from `df56f54`. The
  four untracked files match `origin/main`. Diff before removing.
- `calibratedsports-web-b07`: `M lib/schema.generated.ts`. It is a generated file; check that the
  change is only a regeneration before removing.

Generated 2026-09-22 22:43 Eastern Daylight Time by `research/f09_scratch_inventory.py`. Sizes never follow a junction.

| verdict | dirs | GB |
|---|---:|---:|
| KEEP | 2 | 0.02 |
| LIVE | 4 | 0.01 |
| LIVE? | 41 | 0.76 |
| LOOK | 4 | 0.18 |
| NOT SCRATCH | 1 | 0.89 |
| REMOVE | 112 | 5.79 |

| directory | GB | files | idle min | git | branch | on remote | verdict |
|---|---:|---:|---:|---|---|---|---|
| `C:\Users\Ethan Davis\code\calibratedsports-web-b03` | 0.12 | 489 | 1267.1 | worktree | b-03-cfb-pages | on:origin/a-09-live-prices-contract | REMOVE after unlinking 1 junction(s) with rmdir - then git worktree remove (no --force) |
| `C:\Users\Ethan Davis\code\calibratedsports-web-b07` | 0.13 | 539 | 304.5 | worktree | b-07-mlb-source-label | on:origin/a-09-live-prices-contract | LOOK - uncommitted changes; diff before removing |
| `C:\Users\Ethan Davis\code\cs-analytics-f06` | 0.01 | 518 | 380.7 | worktree | f-06-drafting-scope | on:origin/f-06-drafting-scope | REMOVE - git worktree remove (no --force) |
| `C:\Users\Ethan Davis\code\cs-analytics-f07` | 0.01 | 529 | 258.7 | worktree | f-07-drafting-terms | on:origin/f-07-drafting-terms | REMOVE - git worktree remove (no --force) |
| `C:\Users\Ethan Davis\code\cs-analytics-f08` | 0.00 | 315 | 15.5 | worktree | f08-verify-components | on:origin/f08-verify-components | LIVE? - written in the last hour; recheck in daylight |
| `C:\Users\Ethan Davis\code\prod` | 0.89 | 20517 | 2.0 | - | - | - | NOT SCRATCH - a-10's production clone (queue/units/a-10-production-checkout.md), in build |
| `D:\temp\a03` | 0.24 | 7612 | 1287.2 | - | - | - | REMOVE - delete |
| `D:\temp\a04` | 0.92 | 26100 | 501.4 | - | - | - | REMOVE - delete |
| `D:\temp\a05` | 0.71 | 18685 | 482.5 | - | - | - | REMOVE - delete |
| `D:\temp\a07` | 0.21 | 3943 | 375.7 | - | - | - | REMOVE - delete |
| `D:\temp\a08-base` | 0.02 | 2189 | 137.2 | clone | (detached) | on:origin/HEAD | REMOVE - delete |
| `D:\temp\a08-clone` | 0.02 | 2396 | 137.8 | clone | a-08-team-series | on:origin/a-08-team-series | REMOVE - delete |
| `D:\temp\a08-clone2` | 0.02 | 2402 | 136.5 | clone | a-08-team-series | on:origin/a-08-team-series | REMOVE - delete |
| `D:\temp\a08-export` | 0.01 | 35 | 140.1 | - | - | - | REMOVE - delete |
| `D:\temp\a08-full-pt` | 0.11 | 1971 | 137.4 | - | - | - | REMOVE - delete |
| `D:\temp\a08-full-pt2` | 0.11 | 1971 | 136.2 | - | - | - | REMOVE - delete |
| `D:\temp\a08-gen` | 0.00 | 6 | 139.7 | - | - | - | REMOVE - delete |
| `D:\temp\a08-pt` | 0.02 | 822 | 143.4 | - | - | - | REMOVE - delete |
| `D:\temp\a09-pt` | 0.00 | 19 | 21.7 | - | - | - | LIVE? - written in the last hour; recheck in daylight |
| `D:\temp\a09-pt-full` | 0.11 | 1985 | 19.5 | - | - | - | LIVE? - written in the last hour; recheck in daylight |
| `D:\temp\a09_clone` | 0.02 | 2456 | 19.3 | clone | a-09-live-prices | on:origin/a-09-live-prices | LIVE? - written in the last hour; recheck in daylight |
| `D:\temp\a09_mut` | 0.00 | 2 | 21.7 | - | - | - | LIVE? - written in the last hour; recheck in daylight |
| `D:\temp\a09_web` | 0.00 | 3 | 20.6 | - | - | - | LIVE? - written in the last hour; recheck in daylight |
| `D:\temp\a10-scratch` | 0.00 | 1 | 0.1 | - | - | - | LIVE - unit running now; do not touch |
| `D:\temp\a10-wt` | 0.00 | 312 | 4.9 | worktree | a-10-production-clone | on:origin/HEAD | LIVE - unit running now; do not touch |
| `D:\temp\a11-suite` | 0.03 | 2442 | 69.3 | clone | a-11-components-table | on:origin/a-11-components-table | REMOVE - delete |
| `D:\temp\a11-web` | 0.02 | 1052 | 76.7 | clone | a-11-components-contract | on:origin/a-11-components-contract | REMOVE - delete |
| `D:\temp\b01` | 0.00 | 18 | 1314.7 | - | - | - | REMOVE - delete |
| `D:\temp\b05-fonts` | 0.00 | 20 | 475.1 | - | - | - | REMOVE - delete |
| `D:\temp\b05-probe` | 0.00 | 6 | 357.6 | - | - | - | REMOVE - delete |
| `D:\temp\b05-shots-branch` | 0.01 | 41 | 348.9 | - | - | - | REMOVE - delete |
| `D:\temp\b05-shots-main` | 0.01 | 41 | 348.5 | - | - | - | REMOVE - delete |
| `D:\temp\b05-verify` | 0.00 | 25 | 390.1 | - | - | - | REMOVE - delete |
| `D:\temp\b06` | 0.00 | 70 | 330.7 | - | - | - | REMOVE - delete |
| `D:\temp\b07` | 0.00 | 49 | 303.5 | - | - | - | REMOVE - delete |
| `D:\temp\b10` | 0.00 | 12 | 176.5 | - | - | - | REMOVE - delete |
| `D:\temp\b15` | 0.12 | 27539 | 57.5 | - | - | - | LIVE? - written in the last hour; recheck in daylight |
| `D:\temp\b17-bf` | 0.00 | 16 | 13.3 | - | - | - | LIVE? - written in the last hour; recheck in daylight |
| `D:\temp\b17-comp` | 0.03 | 31 | 23.2 | - | - | - | LIVE? - written in the last hour; recheck in daylight |
| `D:\temp\b17-nocomp` | 0.00 | 3 | 12.5 | - | - | - | LIVE? - written in the last hour; recheck in daylight |
| `D:\temp\c01_clone` | 0.02 | 1252 | 1317.9 | clone | main | on:origin/HEAD | LOOK - uncommitted changes; diff before removing |
| `D:\temp\c01_full` | 0.10 | 1792 | 1317.0 | - | - | - | REMOVE - delete |
| `D:\temp\c01_out` | 0.00 | 1 | 1312.7 | - | - | - | REMOVE - delete |
| `D:\temp\c01_pristine` | 0.01 | 1057 | 1318.0 | clone | main | on:origin/HEAD | REMOVE - delete |
| `D:\temp\c01_pytest` | 0.00 | 39 | 1318.6 | - | - | - | REMOVE - delete |
| `D:\temp\c02` | 0.12 | 3111 | 1302.0 | - | - | - | REMOVE - delete |
| `D:\temp\c03` | 0.29 | 8020 | 1272.4 | - | - | - | REMOVE - delete |
| `D:\temp\c04-wt` | 0.01 | 489 | 504.3 | worktree | c-04-verify-a03 | NOT-ON-REMOTE | KEEP - holds commits on no remote branch; push first |
| `D:\temp\c04_pt` | 0.10 | 1792 | 505.2 | - | - | - | REMOVE - delete |
| `D:\temp\c04_pt1` | 0.00 | 4 | 505.0 | - | - | - | REMOVE - delete |
| `D:\temp\c04_pt2` | 0.00 | 4 | 505.0 | - | - | - | REMOVE - delete |
| `D:\temp\c04_pt3` | 0.00 | 4 | 505.0 | - | - | - | REMOVE - delete |
| `D:\temp\c04_pt4` | 0.00 | 4 | 505.0 | - | - | - | REMOVE - delete |
| `D:\temp\c04_pt5` | 0.00 | 4 | 505.0 | - | - | - | REMOVE - delete |
| `D:\temp\c04_walprobe` | 0.00 | 3 | 507.2 | - | - | - | REMOVE - delete |
| `D:\temp\c05-pt` | 0.10 | 1893 | 490.2 | - | - | - | REMOVE - delete |
| `D:\temp\c05-wt` | 0.01 | 517 | 489.8 | worktree | c-05-cfb-publish | SUPERSEDED | REMOVE - git worktree remove (no --force) |
| `D:\temp\c06` | 0.14 | 1897 | 476.4 | - | - | - | REMOVE - delete |
| `D:\temp\c06-wt` | 0.01 | 525 | 478.8 | worktree | c-06-nba-nhl-terms | NOT-ON-REMOTE | KEEP - holds commits on no remote branch; push first |
| `D:\temp\c07-clone` | 0.02 | 1486 | 376.9 | clone | c-07-mlb-attribution | on:origin/c-07-mlb-attribution | REMOVE - delete |
| `D:\temp\c07-scratch` | 0.18 | 6865 | 375.0 | - | - | - | REMOVE - delete |
| `D:\temp\c07-wt` | 0.01 | 339 | 377.4 | worktree | c-07-mlb-attribution | on:origin/c-07-mlb-attribution | REMOVE - git worktree remove (no --force) |
| `D:\temp\c09-full` | 0.11 | 2003 | 259.7 | - | - | - | REMOVE - delete |
| `D:\temp\c09-pt` | 0.01 | 128 | 263.1 | - | - | - | REMOVE - delete |
| `D:\temp\c09-pt2` | 0.01 | 136 | 262.8 | - | - | - | REMOVE - delete |
| `D:\temp\c09-wt` | 0.01 | 524 | 260.4 | worktree | c-09-held-sports | on:origin/c-09-held-sports | REMOVE - git worktree remove (no --force) |
| `D:\temp\c10` | 0.05 | 288 | 126.2 | - | - | - | REMOVE - delete |
| `D:\temp\c10-pt` | 0.11 | 2012 | 128.7 | - | - | - | REMOVE - delete |
| `D:\temp\c10-wt` | 0.01 | 528 | 128.7 | worktree | c-10-cfb-publish | on:origin/HEAD | REMOVE - git worktree remove (no --force) |
| `D:\temp\c11-scratch` | 0.12 | 2084 | 6.8 | - | - | - | LIVE? - written in the last hour; recheck in daylight |
| `D:\temp\c11-wt` | 0.01 | 529 | 12.6 | worktree | c-11-mlb-cutoff | on:origin/c-11-mlb-cutoff | LIVE? - written in the last hour; recheck in daylight |
| `D:\temp\c12` | 0.12 | 2062 | 20.2 | - | - | - | LIVE? - written in the last hour; recheck in daylight |
| `D:\temp\c12-wt` | 0.01 | 527 | 22.0 | worktree | c-12-verify-served | on:origin/c-12-verify-served | LIVE? - written in the last hour; recheck in daylight |
| `D:\temp\cfb_export` | 0.01 | 141 | 6080.4 | - | - | - | REMOVE - delete |
| `D:\temp\cfb_export2` | 0.01 | 141 | 4771.8 | - | - | - | REMOVE - delete |
| `D:\temp\f01` | 0.00 | 4 | 1355.3 | - | - | - | REMOVE - delete |
| `D:\temp\f02` | 0.00 | 12 | 1306.6 | - | - | - | REMOVE - delete |
| `D:\temp\f02_clone` | 0.02 | 1266 | 1309.3 | clone | main | on:origin/HEAD | LOOK - uncommitted changes; diff before removing |
| `D:\temp\f02_clone_bt` | 0.10 | 1794 | 1308.6 | - | - | - | REMOVE - delete |
| `D:\temp\f02_clone_bt2` | 0.00 | 41 | 1308.4 | - | - | - | REMOVE - delete |
| `D:\temp\f03` | 0.54 | 12180 | 494.2 | worktree (nested) | f-03-verify-c03 | on:origin/f-03-verify-c03 | REMOVE - git worktree remove (no --force) |
| `D:\temp\f04` | 0.23 | 4345 | 476.0 | worktree (nested) | f-04-pfr-alias | on:origin/f-04-pfr-alias | REMOVE - git worktree remove (no --force) |
| `D:\temp\f05` | 0.10 | 2096 | 443.9 | - | - | - | REMOVE - delete |
| `D:\temp\f05-wt` | 0.01 | 527 | 443.8 | worktree | f-05-nfl-weather | on:origin/f-05-nfl-weather | REMOVE - git worktree remove (no --force) |
| `D:\temp\f06` | 0.22 | 5178 | 379.9 | - | - | - | REMOVE - delete |
| `D:\temp\f07` | 0.23 | 5434 | 257.1 | - | - | - | REMOVE - delete |
| `D:\temp\f07_clone` | 0.02 | 1221 | 1322.8 | clone | main | on:origin/HEAD | LOOK - uncommitted changes; diff before removing |
| `D:\temp\f07_suite_tmp` | 0.09 | 1753 | 1324.2 | - | - | - | REMOVE - delete |
| `D:\temp\f07_suite_tmp3` | 0.09 | 1753 | 1322.2 | - | - | - | REMOVE - delete |
| `D:\temp\f08` | 0.21 | 3604 | 14.0 | - | - | - | LIVE? - written in the last hour; recheck in daylight |
| `D:\temp\f09` | 0.00 | 12 | 0.0 | - | - | - | LIVE - unit running now; do not touch |
| `D:\temp\f09-wt` | 0.00 | 313 | 2.2 | worktree | f-09-scratch-inventory | on:origin/HEAD | LIVE - unit running now; do not touch |
| `D:\temp\miniflare-06f17e98b4c4d9d330d09ed2ed6f5fe3` | 0.00 | 3 | 8.1 | - | - | - | LIVE? - written in the last hour; recheck in daylight |
| `D:\temp\miniflare-12de0acdae137c82c6f769a0f47e3188` | 0.00 | 3 | 57.9 | - | - | - | LIVE? - written in the last hour; recheck in daylight |
| `D:\temp\miniflare-1641f9df3ea620273f5e399a93cf93d1` | 0.00 | 3 | 24.5 | - | - | - | LIVE? - written in the last hour; recheck in daylight |
| `D:\temp\miniflare-2852801c627f07e6d493fec8bfc45211` | 0.00 | 3 | 16.9 | - | - | - | LIVE? - written in the last hour; recheck in daylight |
| `D:\temp\miniflare-2f00ce66bf4a9240f31087a7ef270c22` | 0.00 | 3 | 12.0 | - | - | - | LIVE? - written in the last hour; recheck in daylight |
| `D:\temp\miniflare-2fa3a787222fc5e4d3edb4083ebea0ea` | 0.00 | 3 | 25.4 | - | - | - | LIVE? - written in the last hour; recheck in daylight |
| `D:\temp\miniflare-3428423bac929f50cd537a777a4e3590` | 0.00 | 3 | 17.0 | - | - | - | LIVE? - written in the last hour; recheck in daylight |
| `D:\temp\miniflare-4a54c3c654c4eb99527f576176116509` | 0.00 | 3 | 16.5 | - | - | - | LIVE? - written in the last hour; recheck in daylight |
| `D:\temp\miniflare-4fdbf2a2f11fee4be23c14cb078fce73` | 0.00 | 3 | 12.4 | - | - | - | LIVE? - written in the last hour; recheck in daylight |
| `D:\temp\miniflare-55d8f5d6dee89a6f2be9c4282a3772ec` | 0.00 | 3 | 17.1 | - | - | - | LIVE? - written in the last hour; recheck in daylight |
| `D:\temp\miniflare-7c4a66659717f55b5f77a94784c5f871` | 0.00 | 3 | 11.6 | - | - | - | LIVE? - written in the last hour; recheck in daylight |
| `D:\temp\miniflare-989d64207f2abc1fa616685e20e4e36d` | 0.00 | 3 | 11.9 | - | - | - | LIVE? - written in the last hour; recheck in daylight |
| `D:\temp\miniflare-9adec613bb2fec01b294bb1154134617` | 0.00 | 3 | 12.6 | - | - | - | LIVE? - written in the last hour; recheck in daylight |
| `D:\temp\miniflare-9c5b121d624f9d68982121c6061a8be5` | 0.00 | 3 | 12.5 | - | - | - | LIVE? - written in the last hour; recheck in daylight |
| `D:\temp\miniflare-a320f29410bb1aa247a4a5a71605728d` | 0.00 | 3 | 12.0 | - | - | - | LIVE? - written in the last hour; recheck in daylight |
| `D:\temp\miniflare-a914c42d22df60bd8a13b02534ec895c` | 0.00 | 3 | 10.8 | - | - | - | LIVE? - written in the last hour; recheck in daylight |
| `D:\temp\miniflare-abb520ae94b51baada38c638a927cf03` | 0.00 | 3 | 7.7 | - | - | - | LIVE? - written in the last hour; recheck in daylight |
| `D:\temp\miniflare-aed2d6dc77ffe4d5b1fe31b4ad0c6e85` | 0.00 | 3 | 25.6 | - | - | - | LIVE? - written in the last hour; recheck in daylight |
| `D:\temp\miniflare-bfc8562a9748b7cd8feec06ed4dbbc99` | 0.00 | 3 | 23.0 | - | - | - | LIVE? - written in the last hour; recheck in daylight |
| `D:\temp\miniflare-c34d037cc3da483945af12bfc5193b9a` | 0.00 | 3 | 12.6 | - | - | - | LIVE? - written in the last hour; recheck in daylight |
| `D:\temp\miniflare-c52a62f4a7f54337d65fc1ba68350f20` | 0.00 | 3 | 25.5 | - | - | - | LIVE? - written in the last hour; recheck in daylight |
| `D:\temp\miniflare-cc234619d029cdd41327b3687f34b9ea` | 0.00 | 3 | 59.3 | - | - | - | LIVE? - written in the last hour; recheck in daylight |
| `D:\temp\miniflare-cd0ffdbe7af7af37980f7aa681fb989b` | 0.00 | 3 | 11.9 | - | - | - | LIVE? - written in the last hour; recheck in daylight |
| `D:\temp\miniflare-d3e37e094f0f0fb3192d5c0bd200489a` | 0.00 | 3 | 16.9 | - | - | - | LIVE? - written in the last hour; recheck in daylight |
| `D:\temp\miniflare-f0733e57bbb0496cc937291eeae5552e` | 0.00 | 3 | 9.4 | - | - | - | LIVE? - written in the last hour; recheck in daylight |
| `D:\temp\miniflare-f8cd1a34f9da6cab3c1d9132dc98a7f0` | 0.00 | 3 | 25.6 | - | - | - | LIVE? - written in the last hour; recheck in daylight |
| `D:\temp\pt-a11` | 0.00 | 81 | 75.5 | - | - | - | REMOVE - delete |
| `D:\temp\pt-a11-full` | 0.10 | 2024 | 69.6 | - | - | - | REMOVE - delete |
| `D:\temp\pytest-of-Ethan Davis` | 0.01 | 194 | 6254.5 | - | - | - | REMOVE - delete |
| `D:\temp\wt-b08` | 0.00 | 87 | 248.8 | - | - | - | REMOVE - delete |
| `D:\temp\cfb_cap_*` (7 dirs) | 0.002 | 7 | - | - | - | - | REMOVE - delete |
| `D:\temp\miniflare-*` (16 dirs) | 0.001 | 48 | - | - | - | - | REMOVE - delete |
| `D:\temp\open-next-tmp*` (7 dirs) | 0.000 | 14 | - | - | - | - | REMOVE - delete |
| `D:\temp\playwright*` (12 dirs) | 0.000 | 174 | - | - | - | - | REMOVE - delete |

## 4. How to remove them: for Ethan, not run tonight

Only for REMOVE rows, and in this order per directory:

1. **Unlink junctions first, with `rmdir`. Never use `rm -rf` or `--force`.** In `cmd`,
   `rmdir "C:\Users\Ethan Davis\code\calibratedsports-web-b03\node_modules"` removes the link and
   leaves its target alone. PowerShell's `Remove-Item -Recurse` on a junction is the unsafe one.
2. **For registered worktrees, run `git -C <owning clone> worktree remove <path>` without
   `--force`.** If it refuses, the refusal is information (untracked or modified files). Read it;
   don't force past it. Owning clones:
   - f06, f07, f08, f03\wt, f04\wt, f05-wt, f09-wt → `cs-analytics`
   - c04-wt to c12-wt → `cs-cfb`
   - b03, b07 → `calibratedsports-web`
   - a10-wt → `calibrated-sports`
3. **For `D:\temp\f03` and `D:\temp\f04`, remove the nested `wt` worktree first**, then the
   parent directory.
4. Everything else under `D:\temp\<unit>` is plain files. Delete it.
5. Afterwards, run `git worktree prune` in each of the four clones. Then re-run the script and
   check that the count went where you expected.

## 5. The convention: proposed, not imposed

The pattern is not that units are careless. It is that **nothing owns reclamation**. A unit ends
when its report validates, and the only thing that knows a unit ended is `run/relay.ps1`.

**Where scratch lives: one root per unit, `D:\temp\<unit-id>\`, and nowhere else.**
- **The worktree goes at `D:\temp\<unit-id>\wt`.** It is a worktree of the track's main clone, on
  a branch named for the unit. It never goes beside the repos in `code\`: that is where production
  code, and now the production clone, live, on the full drive.
- **pytest `--basetemp` goes at `D:\temp\<unit-id>\pt`.**
- **The relay sets `TEMP`/`TMP` to `D:\temp\<unit-id>\tmp` for the session.** Today every session
  exports `TEMP='D:\temp'` (visible in the live process command lines). So a tool's leak (miniflare,
  open-next, playwright profiles) lands in a shared directory with no owner. Scoped per unit, those
  leaks are reclaimed along with the unit. That bounds b-16's leak even before it is fixed.
- **Use worktrees, not `git clone <local path>`.** A local-path clone's `origin` is a local repo,
  so from inside it "is this pushed?" cannot be answered. It also duplicates the object store.

**What removes it: the relay, after the unit.** The relay already has the facts this step needs.
1. Check that the report validated, `status` is `done`, and the unit's branch HEAD is on `origin`.
2. If so: `rmdir` every junction under the root, run `git worktree remove` (no `--force`) on every
   registered worktree under it, and delete the rest.
3. If any condition fails (not pushed, dirty, a refusal), **leave it and write one line** to the
   runlog naming why. A reaper that forces is worse than none.

**Rollout.**
- Run the reaper in dry-run for one night: it logs what it would remove and removes nothing.
  Compare that log by hand against this script's output, then switch it to live.
- Have the 11:00 watch run this script and list anything under `D:\temp` older than 24h that the
  reaper left. A leak then shows up the next morning, not when a drive fills. That is the
  three-weeks-of-neglect rule applied to the agents' own working space.
