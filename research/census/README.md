# f-20 page census

`f-20-page-census.csv` records the state of every web route on 2026-09-24. Each route was rendered in
Chromium at 1280 and 390 wide, on both grounds, against PRODUCTION-served data (`/data/*` on the
workers.dev site). Three trees were rendered: the b-26 tip, the b-36 tip (which contains b-28) and
the b-41 tip.

To re-run:

1. Clone the web repo and check out the tree you want.
2. Copy `prodBucket.ts` to `lib/`.
3. Point `bucket()` in `lib/serverData.ts` and the handler in `app/data/[...key]/route.ts` at it.
   Do this in the scratch clone only, and never commit it.
4. Run `next build`, then `next start -p <port>`, with `CENSUS_LOG=<file>` set.
5. Run `node census.mjs <tree> <port> <outdir>`, then `python summ.py <tree>`.

`CENSUS_LOG` records every data key the server read and the HTTP status it got. `build_csv.py`
writes the CSV. The judgments in it come from the census output, and the evidence column says which
kind each row rests on.
