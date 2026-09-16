# Design sources — and where the build departs from them

The `.dc.html` files and PNGs in this directory are **not tracked**, by decision.
`.gitignore` carries `design/*` with an exception for this README.

## Why the files are ignored

This repository is public, and the design files carry **mock figures**:

| In the design | Real, at time of writing |
|---|---|
| `Markets priced 1,284` | **11** |
| `Ladder rungs 9,418` | **83** |
| Study rows with blank cells and invented intervals | 17 registered hypotheses, all with committed scripts |

On a site whose entire positioning is that every published figure is sourced,
unlabelled mock numbers are the worst possible thing to publish. They were
committed once by a `git add -A` under an unrelated commit message and removed
in `f6a8058` / `c208080`.

They may ship later as a deliberate `docs/design/` commit **with a README
stating the numbers are mock**. Not swept in under something else.

## Departures from the design, and why

The rule: **when a design file and an explicit instruction conflict, the
instruction wins and the departure is recorded here** so it is not
re-litigated.

### The game log carries Season and team on every row (W05 §1)

The design shows a **per-season** log — `Game log · 2026`, rows like
`['02', 'vs ARI', …]` — with no Season column and no team column.

The build departs: **Season on every row, in every view, and the player's own
team per row** rendered as one matchup column (`NE @ SEA`, `PHI vs DAL`).

Why: the design predates the trade requirement. `Opp` alone cannot show a
trade, and a career log that renders a traded player's games without saying
which team he was on is wrong in a way that looks entirely correct. A team
change also draws a labelled rule across the table (`traded · PHI → NE`) under
the default sort.

Group headers were the obvious alternative and were rejected: they break the
moment the reader sorts by another column. Per-row columns survive any sort.

### The fixed frame, not a rolling window (W04 → W05 §2)

The design and the standing decision both specify one fixed 18-week axis that
fills in as the season progresses, with a compare overlay on the same slots.

A rolling 17-game window shipped instead and has been removed entirely — not
kept as a non-default option. An option that silently changes what the axis
*means* is not a preference.

### Motion: 240ms, not 260ms

The design's bar growth is `260ms`. The project's motion budget
(`lib/chrome.ts` `MOTION.chart`) is `240ms`. The token wins; a design file is
not the place a timing constant lives.

### Prop-history colour is unresolved

The design encodes cleared/missed with green (hue 145) and red (hue 25) —
`DIV.o1/o2/o3` and `u1/u2/u3`. This project reserves green and red for **signed
figures** (§11), enforced by `assertNotSigned`.

Note the guard compares **literal strings**, so the design's
`oklch(0.58 0.13 145)` would pass it while still reading as "green means good".
That is a gap in the guard as much as a question about the design, and both are
open.
