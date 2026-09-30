# f-23 — attack on c-27's resolution result (read-only)

c-27 reported dDSC(decomposed - baseline) = +0.0004 [+0.0001, +0.0008] on P1
(NFL receptions + rush attempts, over side, 2023-2025, against the book close).
These scripts attack that number. They read c-27's scratch predictions and c-27's
own scoring module from a checkout of `origin/c-27-decomposed-usage` (0907c0b);
nothing here writes to any store.

    attack.py   seed sensitivity, duplication test, alternative blocks,
                out-of-fold (leave-one-week-out) DSC, shuffled null, recalibration
    extras.py   smoke-run draw count search; Bonferroni headroom over k specifications
    leak.py     instrumented as-of audit of c-27's Forecaster; --plant shows it fires

Inputs are scratch (`D:/temp/f23/repro/predictions.csv`, byte-identical to
c-27's `D:/temp/c27/predictions.csv` after a full re-run) and are not committed.
Findings are in the track-f report for unit f-23.
