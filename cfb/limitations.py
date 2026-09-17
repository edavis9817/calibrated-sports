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
        "id": "cfb.stats_and_usage_only",
        "severity": "structural",
        "title": ("College football supports statistics, usage and per-game results - "
                  "not aggregate hit rates or settlement, and no book closing line"),
        "statement": (
            "No source records whether a college player appeared in a game. CFBD "
            "publishes no snap or participation field; ESPN's box score lists only "
            "players who recorded a stat; ESPN's play participants list only players "
            "credited on a play; and ESPN's game-roster did_not_play flag is False on "
            "every row. A starting-lineup flag is one-sided - it says who started, never "
            "who did not play - and exists only from 2025: no team-game from 2004 to 2024 "
            "flags a starter, and 1,087 of 1,890 team-games in 2025 still carry none. PFF "
            "sells snap counts; nothing free does. Separately, CFBD's sportsbook lines "
            "carry no timestamp, so no book line can be called a close."),
        "consequence": (
            "A game in which a player recorded no statistic cannot be told apart from a "
            "game the player did not dress for. Per-game display is permitted: the posted "
            "line, the actual statistic and whether it cleared or missed for that one game "
            "- shown only where a statistic row exists, and a game with no row is shown as "
            "no record, never as missed. No aggregate hit rate across games is published, "
            "because the direction of its error is set by a choice the data cannot inform: "
            "counting a game with no row as a zero skews the rate toward the under, and "
            "dropping it skews toward the over. No settlement. No closing line from CFBD "
            "book lines, which carry no timestamps. Real exchange closes DO exist for the "
            "games the probe captured between 2026-09-10 and 09-12 (Kalshi 119 games, "
            "Polymarket 129; see cfb.exchange_probe_capture) - those are exchange "
            "probabilities at a known instant before kickoff, not posted book lines, and "
            "are never presented as one."),
        "evidence_keys": ["game_rosters.did_not_play_true_rows",
                          "game_rosters.team_games",
                          "game_rosters.team_games_full_starting_lineup",
                          "game_rosters.team_games_no_starters",
                          "cfbd_lines.games", "cfbd_lines.games_with_lines",
                          "probe.games_with_a_close.cfb_kalshi"],
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
        "id": "cfb.lines_coverage",
        "severity": "coverage",
        "title": "College game lines are thin before 2018 and sparse on opens and moneylines",
        "statement": (
            "Provider coverage varies by season and by game: 2013-2017 carry only "
            "consensus, numberfire and teamrankings, and retail books appear from 2018. "
            "Opening values are absent on 79% of line rows and moneylines on 80%. "
            "Provider names are normalised (CFBD writes DraftKings two ways); where "
            "both DraftKings feeds quote one game, the fuller feed is kept and the "
            "disagreement is measured."),
        "consequence": (
            "A provider's line exists for some games and seasons and not others; "
            "coverage is a property of the source, not of the games."),
        "evidence_keys": ["cfbd_lines.providers", "cfbd_lines.rows_with_spread_open",
                          "cfbd_lines.rows_with_moneyline",
                          "cfbd_lines.provider_feed_collisions"],
    },
    {
        "id": "cfb.exchange_probe_capture",
        "severity": "provenance",
        "title": "College exchange prices come from one weekend's probe, not the logger",
        "statement": (
            "Every Kalshi and Polymarket college price is from a probe capture between "
            "2026-09-10 19:41Z and 2026-09-13 03:34Z, at non-production cadence: game "
            "markets were polled a median 42s apart on Kalshi and 53s on Polymarket, and "
            "a second copy of the probe ran from 2026-09-11 16:00Z, doubling the poll rate "
            "and corrupting the raw shards it appended to. A close is the last quote "
            "strictly before kickoff, taken a median of 56s and at most 310s before it. "
            "Polymarket quotes an empty book as 0/1; those closes are labelled, not priced. "
            "Kalshi closes cover 119 games and Polymarket 129."),
        "consequence": (
            "One weekend, at poll-limited resolution: a probe close is a genuine close - "
            "a real exchange price at a known instant a median 56s before kickoff - for "
            "those games only, and no college exchange history exists outside that "
            "window. It is an exchange probability, not a sportsbook line: a spread "
            "cannot be derived from it without assuming a scoring distribution, and "
            "coverage skews to marquee games."),
        "evidence_keys": ["probe.poll_gap_median_s.cfb_kalshi.quotes:game",
                          "probe.poll_gap_median_s.cfb_polymarket.quotes:game",
                          "probe.game_polls_per_hour.cfb_kalshi.after_double_write",
                          "probe.close_age_median_s", "probe.close_age_max_s",
                          "probe.games_with_a_close.cfb_kalshi",
                          "probe.games_with_a_close.cfb_polymarket",
                          "probe.close_book_state.cfb_polymarket.empty_0_1"],
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
    """Upsert every limitation, and remove any the code no longer states - a
    retired entry left in the table would still render on the About page."""
    now = time.time()
    ids = [x["id"] for x in LIMITATIONS]
    conn.execute(f"DELETE FROM cfb_limitations WHERE id NOT IN ({','.join('?' * len(ids))})", ids)
    conn.executemany(
        "INSERT INTO cfb_limitations (id, severity, title, statement, consequence, "
        "evidence_keys, recorded_ts) VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET severity=excluded.severity, title=excluded.title, "
        "statement=excluded.statement, consequence=excluded.consequence, "
        "evidence_keys=excluded.evidence_keys, recorded_ts=excluded.recorded_ts",
        [(x["id"], x["severity"], x["title"], x["statement"], x["consequence"],
          json.dumps(x["evidence_keys"]), now) for x in LIMITATIONS])
