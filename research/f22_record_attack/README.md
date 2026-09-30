# f-22 — adversarial audit of a-57's record files

Read-only attack scripts against `origin/a-57-record-three-tiers` (`fb32a07`). They are run
with that branch checked out as a worktree and put on `sys.path`; they import nothing from this
branch and are not collected by pytest. `market_log.db` is opened `mode=ro` only.

| script | what it checks |
|---|---|
| `reconcile.py` | counts, hit rate, break-even, units re-derived from the raw ledger without `core.record` |
| `prices.py` | every lean's ledgered price against the per-book quotes in the Board read it names |
| `kickoff.py` | the ledger's `kickoff_ts` against `nfl_games`, and the minimum pre-kickoff margin |
| `grades.py` | every graded result against its ledgered actual and against `nfl_player_week` |
| `attack.py` | 25 constructed inputs against `build_published` / `build_research` / `build_backtest` |

Outputs and the report: `_relay/reports/f-22-evidence/`, `_relay/reports/track-f.md`.
