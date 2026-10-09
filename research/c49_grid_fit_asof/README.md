# c-49 - `grid_fit`'s mask, made as-of

Pre-registration: `docs/C49-grid-fit-asof-preregistration.md` (4ceaeca), committed and pushed
before the repair or any script here existed. Finding: `docs/findings/c49-grid-fit-asof.md`.

    walks.py     one walk per process, from --src: a detached worktree of the base commit
                 (e88f114, the whole-walk mask) or this repository (the repair). cfb.db mode=ro.
    analyse.py   reads the eight walks, applies the registered rule -> results/
    runs/2026-10-08   the eight logs of the one run (numpy overflow warnings stripped) and the
                 planted game

The planted case is track F's, copied from `origin/f-32-grid-fit-mask` (b498d2c)
`research/f32_grid_mask/plant_mask.py` and `mask_audit.py`. That directory is not edited.

    git worktree add --detach D:/temp/c49/base e88f114
    python research/c49_grid_fit_asof/walks.py --src D:/temp/c49/base --grid a1 --db <cfb.db> \
        --out D:/temp/c49/base_a1.json --make-plant D:/temp/c49/plant.json
    python research/c49_grid_fit_asof/walks.py --src . --grid a1 --db <cfb.db> --out D:/temp/c49/new_a1.json
    ... the same pair with --plant D:/temp/c49/plant.json (-> *_a1_planted.json),
        with --grid plain, and with --grid mov
    python research/c49_grid_fit_asof/analyse.py --dir D:/temp/c49 \
        --published research/results/cfb_game_forecast.json \
        --json-out research/c49_grid_fit_asof/results/c49_grid_fit_asof.json \
        --log-out research/c49_grid_fit_asof/results/c49_grid_fit_asof.log

Chunked at 8,640 grid points, two walks at a time: the 207,360-point walk took 102-105 s, the
51,840-point one 27 s, the plain one 5 s.
