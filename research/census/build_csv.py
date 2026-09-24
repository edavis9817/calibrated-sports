"""f-20: write f-20-page-census.csv from the census judgments (evidence in the census jsonl + key logs)."""
import csv
import os

H = ["tree", "route", "state", "what_is_missing", "key_or_input", "whose", "moves_on_full_publish", "evidence"]
T1 = "b-26 tip 02b0924"
T2 = "b-36 tip 2145edd (contains b-28)"
T3 = "b-41 tip d041437"
R = []


def a(*x):
    R.append(dict(zip(H, x)))


# BROKEN FIRST
for t in (T1, T2):
    a(t, "/nfl/news", "broken",
      "A CBS Sports CFL story (\"CFL odds, picks, predictions: Hamilton vs. Edmonton part of expert's best bets for Week 17\") renders as NFL news and carries a banned sitewide phrase",
      "news sport filter behind /nfl/news/feed", "track B (web)", "no - code",
      "census banned=['best bets'] on 4/4 renders; string found in /nfl/news/feed JSON")
a(T1, "/nfl/news", "pending-data", "Written here (generator); the Pending line is unconditional in code",
  "a generator feed - does not exist", "feed that does not exist", "no", "data-pending=generator")
a(T2, "/nfl/news", "pending-data", "Teammates' usage when a tagged player is absent; the Pending line is unconditional in code (NewsView.tsx:344)",
  "vacancy.* in the analytics export (a-17, unmerged) plus a web reader", "track A publish then track B reader",
  "no - reader not written", "data-pending=vacancy")

for t in (T1, T2, T3):
    a(t, "/nfl/sources", "complete", "none; the only page allowed 'Placeholder ·'", "lib/notConnected.ts", "-", "n/a",
      "placeholder hits 62/61/60 here and 0 on every other render")

for t in (T1, T2):
    for r in ["/", "/about", "/method", "/research"]:
        a(t, r, "complete", "", "", "-", "n/a", "0 pending, 0 error states, 0 banned phrases, no sideways scroll, 4/4 renders")
    a(t, "/studies/market-calibration", "complete",
      "FLAG: publishes over-side -1.40pp on 43,209 (computed 2026-09-10); a-29 reports the current script no longer reproduces it and registered R18 at -2.43pp REG-only",
      "research/calibration.json", "Ethan/track A (restatement)", "only if a restated file is published",
      "DOM checks; supersession taken from a-29's report, not re-run")
    a(t, "/log", "pending-data", "Reversals", "a log-entry field naming the entry it reverses",
      "producer unit nobody has written", "no", "data-pending=reverses")
    a(t, "/nfl/live", "pending-data",
      "Injury report; per-game prop ladder; pre-game prop snapshot. The server calls ESPN directly: this tree does NOT contain b-41",
      "liveLadder/pregameSnapshot: feeds that do not exist; injuryReport: b-41 + a-23",
      "feeds that do not exist; track B merge of b-41", "no", "3 data-pending; /nfl/live/feed fetched site.api.espn.com")
a(T3, "/nfl/live", "pending-data",
  "Distribution; game price path; pre-game prop snapshot. Renders a-23's single snapshot (written 05:24 ET) under an honest 'nothing has replaced it' banner",
  "live/nfl/snapshot.json (served; stale - a-23's reader is not scheduled)",
  "Ethan (schedule a-23 in production) + feeds that do not exist", "no",
  "photographed 1280 navy; data-pending liveLadder, livePriceHistory, pregameSnapshot")

a(T1, "/nfl", "complete", "", "nfl/manifest.json", "-", "n/a", "photographed 1280 navy")
a(T2, "/nfl", "pending-data", "'On the Board' section draws a heading and one link, nothing else",
  "board/nfl/2026/wk03/index.json (404 in production)", "track A (a-26 unmerged, a-31 in flight) + publish",
  "only if the board lands and is published", "photographed 1280 navy")
a(T1, "/nfl/players", "pending-data", "Leaders banded by interval", "per-row intervals on component rows",
  "producer unit nobody has written (stack 2's redesign drops the need)", "no", "data-pending=rowIntervals")
a(T2, "/nfl/players", "complete", "", "nfl/players/index.json, nfl/components/2026.json", "-", "n/a", "photographed 1280 navy")
a(T1, "/nfl/player/[slug] (11 sampled)", "pending-data", "Week-by-week prop results; the Pending line is unconditional in code",
  "prop_history.games (a-24, unmerged) plus a web reader", "track A publish then track B reader",
  "no - reader not written", "10 of 11 carry data-pending=perSettlement (jerry-rice none); photographed 390")
a(T2, "/nfl/player/[slug] (11 sampled)", "complete",
  "Two caveat lines to check: 'Bye weeks need a schedule join the export does not carry yet' (the manifest carries per-team bye state) and 'Played-but-zero needs snap counts the export does not carry yet' (the game log shows snaps)",
  "-", "track B (claim check)", "n/a", "0 pending across 44 renders; photographed ja-marr-chase")
a(T1, "/nfl/teams", "pending-data", "Pace; the Pending line is unconditional in this tree", "pace.* (already published)",
  "track B (stack 2 reads it)", "no", "data-pending=pace")
a(T2, "/nfl/teams", "complete", "", "", "-", "n/a", "0 pending, 4/4; photographed")
a(T1, "/nfl/team/[slug] (32)", "pending-data", "Rank of 32; per-team play-by-play; points per game under a preset",
  "leagueTable/playerSeasonStats unconditional in code; playByPlay: team_units (a-25)", "track B code; track A a-25",
  "no", "32/32 teams")
a(T2, "/nfl/team/[slug] (32)", "pending-data",
  "ATS result; defensive and special-teams snap share; rank of 32; per-team play-by-play (8 figures); points per game under a preset",
  "atsClose: confirmed closing lines (no producer); defence/special: a-14 --extended (staged); playByPlay: team_units.* (a-25 unmerged, scratch only); leagueTable/playerSeasonStats: unconditional in code",
  "mixed: producer unwritten / track A staged / track B code",
  "partly: playByPlay if a-25 is merged and published; defence/special only if --extended is published",
  "32/32; photographed kc")
a(T1, "/nfl/fantasy", "pending-data", "Air yards; red-zone looks; persistence; residual board; call record; stored weights",
  "components rec_air_yds/rz_* (a-15 --stage air_rz); analytics/nfl/opportunity_residual.{per_game,persistence}.* (10 keys, all 404; a-18/a-21 unmerged); call ledger (no producer); stored preference (none)",
  "track A publish (staged + unmerged); call ledger: producer unwritten; stored weights: decision",
  "air yards/red zone only if that stage is published; residual/persistence only if a-21 is merged and published",
  "photographed 1280 navy; 10 opportunity_residual 404s")
a(T2, "/nfl/fantasy", "pending-data", "Stored weights (this tree carries the pre-b-24 scoring page)",
  "scoring preference store", "decision / track B", "no", "data-pending=storedPreference; photographed paper")
a(T1, "/nfl/analytics", "complete", "", "analytics/nfl/index.json", "-", "n/a", "0 pending")
a(T2, "/nfl/analytics", "pending-data",
  "WR receiving yards beyond opportunity; streak study; this week's superlatives. FLAG: the 'over at the close -1.40pp' card (see /studies row)",
  "opportunity_residual (a-21); a research file for the streak study (no producer); deltas.*.wNN (a-19 unmerged)",
  "track A publish; streak study: producer unwritten",
  "residual and deltas if merged and published; streak study no", "photographed 1280 navy")
a(T2, "/nfl/analytics/[metric] (99)", "complete", "", "analytics/nfl/{metric}.json", "-", "n/a",
  "99/99 return 200 with 0 pending or errors (DOM only; 3 of 99 rendered 4 ways)")
a(T1, "/nfl/analytics/[metric]", "gated", "route not in this tree (404)", "", "-", "n/a", "404, as expected")
for r in ["/nfl/board", "/nfl/board?tab=streaks", "/nfl/board?tab=tracker", "/nfl/board?tab=games", "/nfl/board/scorecard"]:
    a(T2, r, "pending-data",
      "Whole page. It says 'No board has been published yet. Lines post from Tuesday.', which is not true this week (it is Thursday and nothing has posted)",
      "board/nfl/2026/wk03/index.json (404); board kinds not yet in the contract",
      "track A (a-26 unmerged, a-31 in flight)", "only if the board lands and is published", "404 key in the log; text captured")
    a(T1, r, "gated", "route not declared in this tree (404)", "", "-", "n/a", "404")
a(T2, "/nfl/board/scorecard/leans.csv", "gated", "leans off by flag", "FLAGS.boardLeans", "Ethan (audit section 8 item 1)", "no", "404")
for t in (T1, T2):
    for r in ["/cfb", "/cfb/players", "/cfb/teams", "/cfb/analytics", "/cfb/live"]:
        a(t, r, "gated", "CFB export not published; one-lined per S-02 (awaitingExport)",
          "config awaitingExport; sports.json lists nfl only (cfb/manifest.json IS served, 138 teams)",
          "Ethan/track C (publish CFB, flip the flag)", "no - needs the config flag flipped", "awaiting banner 4/4")
    for r in ["/nba", "/mlb", "/nhl"]:
        a(t, r, "gated", "Coming-soon page. The client reads {sport}/manifest.json and logs a 404 console error",
          "{sport}/manifest.json", "Ethan (scope: MLB is in season)", "no", "data404 + console error on every render")
    a(t, "/ncaab, /golf", "gated", "rail tile is not a link; the route 404s", "", "-", "n/a", "curl 404")

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "f-20-page-census.csv")
with open(out, "w", newline="", encoding="utf8") as f:
    w = csv.DictWriter(f, H)
    w.writeheader()
    w.writerows(R)
print(len(R), "rows")
