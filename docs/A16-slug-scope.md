# A16 — what widening scope does to published slugs

Unit a-16, 2026-09-23. Script: `research/slug_scope_diff.py` (read-only; runs the
export's own `assign_slugs` against the committed `web/slugs/nfl.json` and writes
nothing).

## What the registry does

`jobs/export_web.assign_slugs`: **registry entries are never changed.** Only ids
absent from the registry get a slug. "Most regular-season career games" ranks
namesakes **arriving in the same run**, and the winner takes the bare slug **only
if it is free**. CLAUDE.md already says this ("A later arrival only gets a bare
slug if it is free"), and `tests/test_export_web.py` already asserts that a
300-game newcomer named Josh Allen gets `josh-allen-2026`.

So widening scope **cannot move an existing URL**. What it can do is hand a
better-known newcomer a year suffix, permanently, because a lesser namesake got to
the bare slug first. That is not recurring URL churn. It is a fixed-at-arrival
choice that gets worse each time scope widens: every widening appends, and never
reassigns.

## Measured

| store | extended newcomers | suffixed | against an existing holder | newcomer has more REG games | existing slugs changed |
|---|---|---|---|---|---|
| a-14 scratch (re-derived) | 7,009 | 186 | 104 | **48** (46 distinct bare slugs) | **0** |
| live `market_log.db` (mode=ro) | 6,869 | 181 | 103 | **48**, the same 48 | **0** |

- **a-14's 49 used a different games count.** a-14 compared `career.games` from the
  exported summaries. That count includes snap-evidenced weeks with no stat row,
  so a long snapper reads 200. The rule ranks on stat-row regular-season weeks.
  The two lists share 45. a-14's count adds 4: aaron-brewer-2012, chris-davis-2014,
  daniel-thomas-2020 and james-williams-2024. The rule's count adds 3:
  jason-moore-1999, will-allen-2001 and tyler-davis-2024.
- **All 46 bare slugs are served today, for the existing holder.** Each one is a key in
  `.upload_state.json` and is present in the live `/data/nfl/players/index.json`,
  pointing at the same id as the local file (46 of 46). On the live site,
  `/nfl/player/{josh-norman, d-j-williams, chris-jones, michael-bennett}` return 200
  and the four suffixed newcomers return 404. None of the newcomers is published.
- The default profile appends 0 slugs. The live index agrees with the registry on
  3,987 of 3,987 ids.

## Options

**A. Freeze: keep the current rule.** There is nothing to build, because it is what
the code already does. Cost: 48 players get year suffixes for good. By construction of
the extended scope, they are players with defensive or special-teams stat rows and
no offensive usage. Examples are `josh-norman-2012` (123 REG games against 13),
`d-j-williams-2004` (141 against 20) and `chris-jones-2016` (151 against 126). The
number grows with each future widening. Search is unaffected, because it runs on
names. Track B could add an optional "also named" line to a player page, grouping
the players index by name. That needs no contract change.

**B. Reassign and emit redirects.** The bare slug moves to the player with more
games, and the old holder moves to a suffix. The old URL cannot redirect, because
it now serves someone else. Every existing link to `/nfl/player/josh-norman`
therefore silently shows a different person. That is worse than a 404. Track B
would need a redirect map, which means a new R2 key and contract kind plus a Worker
lookup before the 404. The export would need a mutable registry with history. All
of this would recur on every scope change. Not recommended.

**C. Suffix every slug from the start.** Every published URL changes, 3,987 today,
with one permanent redirect each from bare to suffixed. Each redirect is
unambiguous at the cutover. Because no one is ever given a bare slug again, a
reserved bare slug never collides. Track B builds the same redirect map as in B,
but it is written once and never grows. Cost: a one-time rewrite of every URL,
uglier URLs, and the redirect table carried forever. It removes the "who got there
first" problem entirely.

**Recommendation: A.** It breaks nothing, costs nothing, and matches the rule
already written down. Add the "also named" line on the site if the suffixes read
badly. C is the only option that removes the problem rather than accepting it.
Its whole cost is paid in redirects, and it is not worth paying for 48 players.
