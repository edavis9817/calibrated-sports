# c-50 - plain Elo's K grid, widened, and the weeks 1-4 sentence over seeds

Pre-registration: `docs/C50-baseline-grid-edge-preregistration.md` (bc7357b), committed and pushed
before anything here existed. Finding: `docs/findings/c50-baseline-grid-edge.md`.

    panel.py     the seed panel (c-28's resample, 200 seeds x 2,000 draws, pooled) and the
                 registered verdict words. Pure.
    analyse.py   the one registered run: plain Elo on the registered, K-widened (P) and
                 all-widened (S) grids; fitted constants and edges per season; the panel -> results/
    posthoc.py   NOT pre-registered. Per-stage Brier of each forecaster and the
                 baseline-against-baseline difference, written after the registered run.

    LOGGER_DB=<market_log.db> python -m research.c50_baseline_grid_edge.analyse \
        --json-out research/c50_baseline_grid_edge/results/c50_baseline_grid_edge.json \
        --log-out  research/c50_baseline_grid_edge/results/c50_baseline_grid_edge.log

`market_log.db` is opened `mode=ro` (`jobs.season_model.market_log_ro`); only `nfl_games` and
`nfl_teams` are read. The registered run took 427 s (318 s of it the 6,743-game panel); the post
hoc run about 2 minutes. Each was run once.

The registered grid is `jobs.season_model.GRID` - K 10, 15, 20, 25, 30, 40. Plain Elo's fitted K
is 40 in 25 of 25 seasons there. Nothing in `models/`, `jobs/` or `research/game_forecast.py` was
changed by this unit.
