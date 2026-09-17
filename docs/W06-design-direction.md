# W06 — design direction

**Not a build brief.** This records the diagnosis and the reference model so the design pass after §3
starts from a decision rather than from instinct. The build brief comes when §3's data is in hand.

---

## Why the pages read as bland

Three causes, in order of how much they matter.

**1. The composition assumes density the data doesn't have.** `Shell.dc` was validated while full of
mock numbers — 1,284 markets, 9,418 ladder rungs, a 16-week prop history with a bar in every slot. The
real board is 11 markets and 83 rungs, and prop history isn't built. The same layout with a fraction of
the data in it reads as empty, and no amount of colour fixes a composition designed for ten times its
content.

**2. The density budget went to the thinnest layer.** The market covers 11 of 3,970 players, so 99.7%
of player pages render the *absence* of a market. The stats are the opposite — 3,970 players, 7,292
games, eight seasons, eighteen columns a game. The design made the market the centrepiece and the
stats a spine underneath it. The weight belongs where the data is.

**3. Every specification decision this cycle was a subtraction.** Suppress unsourced figures, mark
placeholders, no valence in colour, no gradient on data marks, no team colour on charts, no charting
library. Each was correct in isolation. Cumulatively they specified a site defined by what it declines
to show, and provenance-first became an aesthetic of absence. The rules stay; they are not a substitute
for composition, and they were allowed to become one.

§3 addresses cause 2 directly — prop history is 90,797 settled-and-closed rows, not 11 markets — which
is why it goes first. Causes 1 and 3 are this document's job.

---

## Reference model, per page

Two registers, assigned by what the page is for. A page picks one. Mixing them within a page is what
produces the current flat, uniform-weight look.

**Dense reference** — Pro Football Reference, FBref, Baseball Savant. Density *is* the aesthetic:
tables first, small type, tabular numerals, tight rows, minimal chrome, as much on one screen as will
fit legibly. Sorting and scanning are the primary interactions. Whitespace is a cost, not a feature.

| Page | Register |
|---|---|
| Player page — game log, season and career tables | Dense reference |
| Players index, Teams index | Dense reference |
| Stats leaderboards | Dense reference |
| Team page | Dense reference |
| Fantasy scoring editor | Dense reference — it is a tool, not a story |

**Editorial analytics** — The Athletic, early FiveThirtyEight. Charts carry the page, typography does
the hierarchy, each screen reads as an argument with a conclusion. Whitespace is deliberate and earns
its place by making one thing dominant.

| Page | Register |
|---|---|
| Sport home | Editorial — it exists to show what search cannot |
| Research: Studies, Method, Log, Weekly | Editorial — this is where the case is made |
| Landing (the real one, not v0) | Editorial |
| Live | Editorial |

**The player page is the one hybrid, and the seam must be explicit.** Its charts — usage, fantasy,
prop history — are editorial: large, dominant, one idea each. Its tables are dense reference. The
transition between them is a visible section break, not a gradual drift. Today the whole page sits in
one middling register, which is why it reads as neither.

---

## What adds visual weight without breaking a single provenance rule

In order of effect.

**Density.** A sparse page looks bland irrespective of styling. This is the largest lever by a wide
margin and it is a data problem, not a design one — which is why §3 comes first.

**Typographic hierarchy.** Currently tiles, headings and table text sit within a narrow size range, so
nothing dominates. Display-scale numerals for the figures that matter, real steps between levels, mono
reserved for data rather than applied to everything.

**Chart variety.** One chart form (bars) across three sections is monotony. Career arcs, distributions
of a player's own weekly output, small multiples across seasons, the survival curve at multiple scales
— all data-driven, all buildable, all adding visual interest that is *information* rather than
decoration.

**The accent gradient on chrome.** Section headers, hero panels, the active-nav rule. Tokens already
defined; contrast caution on the cyan end stands.

**Team colour, extended slightly.** Chips landed but barely register. A team-coloured hairline on the
player identity band is identity, not data encoding, and adds a great deal for one rule. Charts stay
off-limits.

**Weight and rhythm in tables.** Zebra or grouped banding, emphasised season boundaries, de-emphasised
zero cells. A dense table is only a virtue if it is scannable.

---

## Rules that do not move

- No figure unsourced. Query-derived or visibly marked placeholder.
- Team colour is identity only, never a chart fill or row background.
- Gradient never on a mark that encodes a number.
- Cleared/missed is non-valenced diverging, hue-guarded.
- Empty states distinguishable by form, not hue alone.
- `cleared` / `missed`, never win / loss.
- No charting library; hand-drawn SVG.
- Works at 400px.

None of these caused the blandness. Composition did.

---

## Sequencing

1. **§3** — schedule join + settlement fix, components table, custom scoring, dirty set, prop-history
   export. Unblocks prop history, leaderboards, the fantasy editor and the third empty state.
2. **Then** a design pass against this document, per page, with the register chosen before any styling
   is written.
3. The design files in `design/` are a first draft validated against mock data. Departures are expected
   and get recorded, not requested.
