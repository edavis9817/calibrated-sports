# f-32 - the `grid_fit` mask that lets a season see its own future

Read-only audit of `models/cfb_game.py::grid_fit` at `origin/c-39-cfb-game-model`.
`PREREGISTRATION.md` was committed and pushed (3d1ea73) before any figure existed.

    mask_audit.py    the walks (c-39's own code from a detached worktree; cfb.db mode=ro)
    analyse.py       reads them, applies the pre-registered rule -> results/f32_mask_audit.json
    labels_asof.py   the conference/division label test -> results/f32_labels_asof.json
    plant_mask.py    POST-HOC, outside the rule: one synthetic future game on an a = 1.0 grid
    runs/2026-10-08  every log of the run this unit reports (numpy overflow warnings stripped)

Result on cfb.db as of 2026-10-08, registered grid (207,360 points): verdict (a) harmless.
The mask removes 224 to 9,097 points from a scored season's as-of choice set; a genuine
truncated `C.grid_fit` refit for every one of 2005-2026 returns the walk's constants; the
full-pipeline scramble moved 0 of 7,752 pre-cutoff forecasts while its plant moved 272.
The channel is real all the same: plant_mask.py moves 1,378 forecasts from 2005-06 with
one game dated after all of them. The repair belongs to track C.

`--chunk` matters: fourteen unchunked 207,360-point walks at once ran ~5x slower each
(1.66 MB vectors, memory-bound). Chunked at 8,640 points a full walk is ~3.5 minutes and
the sums are bit-identical to the unchunked reference (max |diff| 0.0).
