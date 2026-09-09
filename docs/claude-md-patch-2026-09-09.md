# CLAUDE.md patch — 2026-09-09 re-verification

The working agreement is not a file in this repo (searched the repo, its parent,
and `~/.claude/`; only the unrelated global primer exists). These are the two
edits the re-verification calls for — paste them into wherever the agreement
actually lives.

## Edit 1 — replace the priority-markets bullet

Under **Settled by evidence**, replace:

```
- **Priority markets**: targets (.277), rush attempts (.254), receptions (.234)
  — not yards (.145), TDs (~.05), or QB props (.039).
```

with:

```
- **Priority markets**: targets, rush attempts, receptions — not yards (.145),
  TDs (~.05), or QB props (.039). The SET was re-verified 2026-09-09 on unseen
  2025 and holds: those three lead in every window, both data sources and both
  position-pool definitions. The internal ORDER and the R² levels are **not**
  currently reproducible — see the methodology note below.
```

## Edit 2 — append to the same section

```
### The original research methodology is lost (2026-09-09)

There is no `research/` study behind the figures above — no notebook, no saved
fit, no sample screen, only the numbers. `research/reverify.py` is a documented
*reconstruction*, not a replication, so:

- **Comparisons of level against these figures are confounded** and must not be
  read as evidence about the data. A reconstructed usage-only baseline lands
  ~0.2 R² above every reference value and ranks rush attempts ahead of targets.
- **The distribution parameters do not reproduce** under any sample filter
  tried. The family choices stand (overdispersed, right-skewed, non-trivial
  zero mass, all confirmed on 2025) but `core/distributions.py` and
  `tests/test_core.py` hardcode moments — 3.75 mean / 1.69 var-to-mean
  receptions, 12.1 / 3.72 carries — that measured out at 2.3–3.2 / 1.8–2.3 and
  7.5–9.3 / 4.7–6.4. Treat those constants as unverified.
- **What IS attributable**: the dead `player_stats` release did not corrupt
  anything. The same pipeline over both sources differs by ≤0.012 on every
  market, with identical ordering.

Recovering or replacing the sample screen is a prerequisite to re-opening any
of the settled findings on levels.
```

Reports behind this: `research/results/pass1-replication.txt`,
`pass1-ab-legacy.txt`, `pass2-holdout.txt`.
