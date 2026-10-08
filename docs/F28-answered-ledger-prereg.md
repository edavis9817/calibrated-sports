# f-28 - pre-registration: how many distinct questions the `needs_ethan` pile holds

Written and committed BEFORE any similarity between two items was computed. The
commit that adds this file is the evidence of order; the script that implements
it (`relay/items.py`) lands in a later commit.

## What I had seen when this was written

- The item count: 554 `needs_ethan` entries in 238 machine reports under
  `_relay/reports/json/` on 2026-10-08. The brief's 465 is f-24's snapshot of
  2026-10-06 (203 of 212 reports) and is stale, not wrong.
- The first 160 characters of `what` for 196 of the 554 items (units a-01
  through b-04, in file order). No pair was scored, no cluster formed.

## What counts as the same question

Two items are the same question when **one answer from Ethan - one decision, or
one action performed once - would settle both.** "Merge branch X" raised by two
units is one question. "Merge branch X" and "merge branch Y" are two, even
though they are the same KIND of ask.

The rule below reads text only. It does not read git, so it cannot know that
merging one stacked branch covers another; where the text does not say so, the
two stay apart.

## The rule

1. **Population.** Every entry of every `needs_ethan` array in
   `_relay/reports/json/*.json` at run time. The run prints the report count,
   the item count and a hash of the sorted `(item id, what)` list.
2. **Item id.** `<unit_id>#<index>`: the report's `unit_id` and the entry's
   0-based position in its `needs_ethan` array. Reproducible from the reports
   alone. A 12-hex SHA-256 of the `what` text travels with it so a report edited
   under an id is detectable.
3. **Text.** The `what` field only. `why` and `recommended` are not read.
4. **Normalise.** Lowercase. A unit or branch reference
   `[abcf]-<1 to 3 digits>` with any `-slug` suffix becomes one token
   `u_<letter>_<number>` (so `a-68-prop-mapping-collapse` and `a-68` are the same
   token). Tokens are then `[a-z0-9_]+`; tokens of length 1 are dropped; no
   stemming. Stopwords, fixed here: the, an, to, of, and, or, in, on, for, is,
   it, be, by, at, as, with, from, that, this, then, before, after, whether,
   which, so, not, are, do, into, its, if.
5. **Weight.** Each item is the SET of its tokens (a token counts once). idf =
   ln(N / df), N the item count, df the number of items containing the token.
   Similarity is the cosine of the two idf vectors.
6. **Link** two items when cosine >= **0.50**. A cluster is a connected
   component of that graph (single linkage). An item with no tokens is its own
   cluster.
7. **Headline figure: the number of clusters under exactly steps 1-6.**

## Declared alongside, not the headline

- **Sensitivity:** the cluster count at 0.40 and at 0.60, printed beside the
  headline. Neither replaces it.
- **Chaining:** single linkage can join two unrelated items through a third. The
  run prints the largest cluster's size and every multi-item cluster's minimum
  internal pairwise cosine.
- **False-merge audit:** I read every multi-item cluster if there are 80 or
  fewer, otherwise a `random.Random(28)` sample of 40, and count clusters holding
  two items that no single answer could settle.
- **Missed-merge audit:** a `random.Random(28)` sample of 60 pairs from
  different clusters with cosine in [0.30, 0.50); I count the pairs that are the
  same question.
- **Merge-class count** (descriptive, a KIND of ask and not a question): items
  whose `what` starts with merge, merging, push, land or cherry-pick, or
  contains "merge order".

Neither audit adjusts the headline. If I change steps 1-6 after seeing output,
the change is labelled post hoc and both counts are reported.

## What I expect, so it can be wrong

A text rule at 0.50 is strict on short imperative sentences, so I expect it to
MISS duplicates more than it invents them: the headline should be read as an
upper bound on distinct questions if the false-merge audit comes back clean, and
as nothing at all if it does not.
