"""What CFB data cannot support, recorded as data rather than as a footnote.

These rows are written to `cfb_limitations` on every ingest run so that an
About/Sources page can render them from the store. Each cites the
`cfb_measurements` keys that evidence it; the figures are recomputed from the
files on every parse, so a page quoting them is quoting a query, not this text.

The prose below carries numbers measured on 2026-09-16 for the record. Where a
page needs a figure it must read the measurement, not parse the sentence.
"""
import json
import time

LIMITATIONS = [
    {
        "id": "cfb.no_appearance_signal",
        "severity": "structural",
        "title": "No record of whether a college player appeared in a game",
        "statement": (
            "No public source records college snap counts or participation. "
            "CFBD publishes no snap or participation field; ESPN's box score lists "
            "only players who recorded a stat; ESPN's play participants list only "
            "players credited on a play; and ESPN's game-roster did_not_play flag "
            "is False on every row measured. A starting-lineup flag exists but is "
            "one-sided - it says who started, never who did not play - and exists "
            "only from 2025: no team-game from 2004 to 2024 flags a starter, and "
            "1,087 of 1,890 team-games in 2025 still carry none. PFF sells snap "
            "counts; nothing free does."),
        "consequence": (
            "A game in which a player recorded no statistic cannot be told apart "
            "from a game the player missed. CFB therefore publishes statistics and "
            "usage only: no hit rates, no prop history, no settlement. Any rate "
            "computed over a college player's games is biased toward games in which "
            "a statistic was recorded, and nothing in the data can correct it."),
        "evidence_keys": ["game_rosters.did_not_play_true_rows",
                          "game_rosters.team_games",
                          "game_rosters.team_games_full_starting_lineup",
                          "game_rosters.team_games_no_starters"],
    },
    {
        "id": "cfb.no_targets_in_box_score",
        "severity": "coverage",
        "title": "Targets come from play-by-play, not the box score",
        "statement": (
            "ESPN's college receiving box score has receptions, yards and "
            "touchdowns but no targets column. Targets, rushes and touches are "
            "counted from play-by-play upstream, per game, and cover fewer games "
            "than the box score in early seasons."),
        "consequence": (
            "Receptions and targets for one player-game can come from different "
            "sources and need not reconcile. Targets thrown to a player upstream "
            "could not identify count toward the team's total and no player's - "
            "about 5% a season from 2004 to 2014 (2,530 of 44,993 in 2011), under "
            "0.5% from 2015 - so player target shares need not sum to one."),
        "evidence_keys": ["player_box.games", "player_usage.games",
                          "player_usage.unattributed_targets"],
    },
    {
        "id": "cfb.no_espn_betting_lines",
        "severity": "provenance",
        "title": "ESPN's historical college lines are not used",
        "statement": (
            "sportsdataverse's espn_cfb_betting file fills games with no line with "
            "a constant spread of 2.5 and, on 6,395 of 6,411 such games, a total "
            "of 55.5 (odds_source 'default') - 6,276 of 6,277 games from 2004 to 2011, and 0 "
            "to 20 games a season since. It is never ingested. Historical college "
            "game lines come from CFBD."),
        "consequence": "No college line predates CFBD's coverage, which starts in 2013.",
        "evidence_keys": [],
    },
    {
        "id": "cfb.team_identity_gaps",
        "severity": "coverage",
        "title": "Some teams in some seasons have games but no team record",
        "statement": (
            "The per-season team file does not always carry every team its games "
            "name. On 2026-09-16 the worst season was 2005, missing 64 of the 298 "
            "team ids its games reference; most seasons miss 0 to 6."),
        "consequence": (
            "A game can name a team that has no name, conference or colours for "
            "that season. The game and its box score still stand."),
        "evidence_keys": ["games.team_ids_without_team_row"],
    },
    {
        "id": "cfb.lines_untimestamped",
        "severity": "provenance",
        "title": "College game lines carry no timestamp",
        "statement": (
            "CFBD reports, per provider, a spread and total and their opening values, "
            "with no time attached to either. The last value CFBD holds is not "
            "necessarily the line at kickoff, and provider coverage varies by season "
            "and by game: 2013-2017 carry only consensus, numberfire and teamrankings, "
            "and retail books appear from 2018. Opening values are absent on 79% of "
            "rows and moneylines on 80%. Provider names are passed through as CFBD "
            "writes them, so one book can appear twice ('DraftKings' and 'Draft "
            "Kings' in 2025)."),
        "consequence": (
            "College lines are published as the provider's reported values, never as "
            "a kickoff close, and no closing-line-value figure is computed from them."),
        "evidence_keys": ["cfbd_lines.games", "cfbd_lines.games_with_lines",
                          "cfbd_lines.providers", "cfbd_lines.rows_with_spread_open",
                          "cfbd_lines.rows_with_moneyline"],
    },
    {
        "id": "cfb.current_season_rosters_partial",
        "severity": "coverage",
        "title": "Current-season rosters fill in as the season is published",
        "statement": (
            "The current season's roster file is rebuilt upstream as teams are "
            "processed; on 2026-09-16 it held 476 players on 4 teams against "
            "26,327 players on 234 teams for 2025."),
        "consequence": "A current-season player can have box-score rows and no roster row.",
        "evidence_keys": ["rosters.rows", "rosters.teams"],
    },
]


def record(conn):
    now = time.time()
    conn.executemany(
        "INSERT INTO cfb_limitations (id, severity, title, statement, consequence, "
        "evidence_keys, recorded_ts) VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET severity=excluded.severity, title=excluded.title, "
        "statement=excluded.statement, consequence=excluded.consequence, "
        "evidence_keys=excluded.evidence_keys, recorded_ts=excluded.recorded_ts",
        [(x["id"], x["severity"], x["title"], x["statement"], x["consequence"],
          json.dumps(x["evidence_keys"]), now) for x in LIMITATIONS])
