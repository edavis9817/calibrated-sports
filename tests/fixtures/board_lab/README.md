# Board and Lab fixtures (unit a-38, 2026-09-26)

Real producer output, for the web units building the **Lines** and **Replay** pages
(DECISIONS-2026-09-26 §2 renames the pages only; the kinds and keys below are the
contract and do not change). Each directory is a **tree root**: the paths under it
are the R2 keys, exactly as the site reads them at `/data/{key}`.

| tree | keys | what it is |
|---|---|---|
| `wk03-upcoming/` | `board/nfl/2026/wk03/index.json`, one `read-*.json`, `board/nfl/ledger.{parquet,csv}` | the first real Board read, 2026-09-26 18:13 UTC, cut to 2 of its 15 games: 34 rows, 16 leans, all `upcoming` |
| `wk02-graded/` | `board/nfl/2026/wk02/index.json`, two reads, the ledger pair | a week-2 REPLAY (`--at 2026-09-17T20:00:00Z`, then a read on 2026-09-26), cut to 3 games: 31 leans = 29 graded + 2 void (`inactive`), ledger 62 events |
| `lab-library/` | `lab/nfl/index.json`, `lab/nfl/presets/*.json` | the Lab library exactly as `jobs.lab_publish` wrote it: 4 presets published (3 LOSES, 1 UNCLEAR), 4 listed unsupported in the index |

**What was changed from the producer's output.** Board trees: rows and ledger events
belonging to games outside the kept set were DROPPED, and each index's `leans` counts were
recomputed from its latest read (`core.board.row_partition`), so the index/read partition
still reconciles. No value was edited. Lab tree: nothing; the committed blobs are byte-identical to
the run's output (a checkout under `core.autocrlf=true` adds CRs) (all four presets are kept because the index names them, and the Lab's
own check refuses an index naming a file that is not there).

**What these do NOT cover.** No `live` row (no real read has been taken while a game was
in progress). No `market_pulled` or `no_snap` void. No `line_moved_after_publication` or
`lean_changed_after_publication` set true. The week-2 replay's first read was taken at a
chosen past instant and its model fit is today's code, so it is a real shape, not a record
of what the Board would have published that Thursday.

`tests/test_board_lab_fixtures.py` runs each producer's own `check_tree` over these
trees, so a contract change that makes them stale fails in this repo's CI.
