"""The manifest's team listing, and the season summary the teams board reads.

Run: pytest -q tests/test_team_entry.py

Track B's A14 and track C's C-3 land on one object. `manifest.teams[]` was
`{slug, abbr, name}`, so a figure per team meant opening every team file on an
index page - the cost `counts` exists to remove - and the board shipped with its
figures marked rather than inventing them.

Two things here are NOT what was asked for, and both are tested because that is
where a faithful implementation would have been wrong:

  * `tied` - A14 asked for {games, cleared, missed}. The sport has ties, so that
    triple silently loses a drawn result.
  * `division` is NOT split into a bare region. The source stores "NFC West" as
    an atom; "West" is recorded nowhere, and inventing the decomposition would
    publish a structure the sport does not have.
"""
import json
import os

import pytest
from jsonschema import Draft202012Validator

import config
from jobs import export_web as E

# The shared store fixture, same import the contract suite uses. Two tests below
# run a real export to prove the run report and the manifest derive their counts
# from one place; without this they collect as ERRORS rather than failures, and
# an error is not a failure - the run still ends in a passing count, which is
# how two tests written to guard the riskiest edit came within one line of being
# decorative.
from tests.test_export_web import NOW, _walk, db  # noqa: F401  (shared fixture)

DEFS = E.CONTRACT["$defs"]


def validator(name):
    return Draft202012Validator({"$ref": f"#/$defs/{name}", "$defs": DEFS})


def summary(**kw):
    base = {"games": 1, "cleared": 1, "missed": 0, "tied": 0,
            "points_for": 36, "points_against": 31, "markets": 4,
            "cumulative": [played_week(1, 1, 0, 0, 36, 31), empty_week(2, "unplayed")]}
    base.update(kw)
    return base


def played_week(index, cleared, missed, tied, pf, pa):
    return {"index": index, "state": "played", "cleared": cleared, "missed": missed,
            "tied": tied, "points_for": pf, "points_against": pa}


def empty_week(index, state):
    return {"index": index, "state": state, "cleared": None, "missed": None,
            "tied": None, "points_for": None, "points_against": None}


def entry(**kw):
    base = {"slug": "buf", "abbr": "BUF", "name": "Buffalo Bills",
            "conference": "AFC", "division": "AFC East", "classification": None,
            "season": summary()}
    base.update(kw)
    return base


# --------------------------------------------------------------- the shapes

def test_a_full_entry_validates():
    assert not list(validator("TeamEntry").iter_errors(entry()))


def test_every_grouping_field_may_be_null_and_none_may_be_ABSENT():
    """Nullable is not optional. A sport says what it has and says null for what
    it has not; an absent key says nothing at all."""
    v = validator("TeamEntry")
    assert not list(v.iter_errors(entry(conference=None, division=None, classification=None)))
    for field in ("conference", "division", "classification", "season"):
        bad = entry()
        bad.pop(field)
        assert list(v.iter_errors(bad)), f"{field} validated while absent"


def test_season_may_be_null_for_a_sport_that_publishes_no_figures():
    assert not list(validator("TeamEntry").iter_errors(entry(season=None)))


def test_the_entry_is_closed():
    assert list(validator("TeamEntry").iter_errors(entry(plays=63))), (
        "an unrequested field must fail the export until the contract moves")


def test_tied_is_required_even_though_it_was_not_requested():
    bad = summary()
    bad.pop("tied")
    assert list(validator("TeamSeasonSummary").iter_errors(bad))


def test_points_may_be_null_before_a_team_has_played():
    assert not list(validator("TeamSeasonSummary").iter_errors(
        summary(games=0, cleared=0, missed=0, tied=0, points_for=None, points_against=None)))


def test_membership_shape():
    v = validator("TeamMembership")
    assert not list(v.iter_errors(
        {"season": 2026, "conference": "AFC", "division": "AFC East", "classification": None}))
    assert list(v.iter_errors({"season": 2026}))


# ------------------------------------------------- what the producer emits

def _game(season, week, home, away, hs, as_, gtype="REG"):
    return {"game_id": f"{season}_{week:02d}_{away}_{home}", "season": season, "week": week,
            "game_type": gtype, "home_team": home, "away_team": away,
            "home_score": hs, "away_score": as_}


def test_the_summary_counts_wins_losses_AND_TIES():
    games = {
        "a": _game(2026, 1, "BUF", "MIA", 24, 20),   # BUF cleared
        "b": _game(2026, 2, "NE", "BUF", 30, 17),    # BUF missed
        "c": _game(2026, 3, "BUF", "NYJ", 21, 21),   # BUF tied
    }
    out = E.team_season_summaries(games, 2026, {})
    buf = out["BUF"]
    assert (buf["games"], buf["cleared"], buf["missed"], buf["tied"]) == (3, 1, 1, 1)
    assert buf["points_for"] == 24 + 17 + 21
    assert buf["points_against"] == 20 + 30 + 21


def test_cleared_missed_and_tied_ALWAYS_SUM_TO_GAMES():
    """The schema cannot express this and it is the whole reason `tied` exists.
    A {games, cleared, missed} triple would fail here on the drawn game."""
    games = {
        "a": _game(2026, 1, "BUF", "MIA", 24, 20),
        "b": _game(2026, 2, "NE", "BUF", 30, 17),
        "c": _game(2026, 3, "BUF", "NYJ", 21, 21),
    }
    for abbr, s in E.team_season_summaries(games, 2026, {}).items():
        assert s["cleared"] + s["missed"] + s["tied"] == s["games"], abbr


def test_an_unplayed_team_reports_null_points_not_zero():
    out = E.team_season_summaries({}, 2026, {})
    buf = out["BUF"]
    assert buf["games"] == 0
    assert buf["points_for"] is None and buf["points_against"] is None
    assert buf["cleared"] == buf["missed"] == buf["tied"] == 0


def test_unplayed_games_and_other_seasons_are_excluded():
    games = {
        "played": _game(2026, 1, "BUF", "MIA", 24, 20),
        "future": _game(2026, 2, "BUF", "NYJ", None, None),
        "lastyr": _game(2025, 1, "BUF", "MIA", 10, 7),
        "post": _game(2026, 19, "BUF", "KC", 27, 24, gtype="WC"),
    }
    assert E.team_season_summaries(games, 2026, {})["BUF"]["games"] == 1


def _market_file(team_slug, *components):
    return {"identity": {"id": "x", "team": team_slug}, "components": list(components)}


def _quoted(stat, rungs=1):
    return {"stat": stat, "basis": "MARKET", "rungs": [{"line": 4.5}] * rungs}


def test_markets_counts_DISTINCT_PRICED_MARKETS_not_priced_players():
    """A player with two stats priced is TWO markets. The card's label says
    markets and its empty state says "No ladder" - both name the market, not the
    person. Counting players was the first version and it was wrong."""
    files = {
        "a": _market_file("buf", _quoted("rec"), _quoted("rush_att")),   # one player, 2
        "b": _market_file("buf", _quoted("rec")),                        # another, 1
        "c": _market_file("mia", _quoted("rec")),
    }
    by_team = E.count_markets_by_team(files)
    out = E.team_season_summaries({}, 2026, by_team)
    assert out["BUF"]["markets"] == 3
    assert out["MIA"]["markets"] == 1
    assert out["NE"]["markets"] == 0


def test_only_QUOTED_components_are_markets():
    """DERIVED and ANCHORED were never quoted, so neither is a market - and a
    MARKET component with an empty ladder is not one either."""
    files = {"a": _market_file(
        "buf",
        _quoted("rec"),
        {"stat": "rec_yds", "basis": "DERIVED", "note": "from receptions"},
        {"stat": "td", "basis": "ANCHORED", "note": "scaled"},
        {"stat": "rush_att", "basis": "MARKET", "rungs": []},   # listed nowhere
    )}
    assert E.count_markets_by_team(files)["buf"] == 1


def test_the_count_joins_on_SLUG_because_that_is_what_a_market_file_holds():
    """`identity.team` is a slug ("buf"); TEAM_NAMES is keyed on the
    abbreviation ("BUF"). Joining one against the other returns 0 for every team
    and looks exactly like a slate with no ladders."""
    by_team = E.count_markets_by_team({"a": _market_file("buf", _quoted("rec"))})
    assert set(by_team) == {"buf"}, "the map is slug-keyed"
    assert E.team_season_summaries({}, 2026, by_team)["BUF"]["markets"] == 1
    # the other answer: an abbr-keyed map must NOT resolve
    assert E.team_season_summaries({}, 2026, {"BUF": 9})["BUF"]["markets"] == 0


def test_a_market_file_with_no_team_is_skipped_not_crashed():
    assert E.count_markets_by_team({"a": {"identity": {}, "components": [_quoted("rec")]}}) == {}


def test_every_published_team_gets_a_row_even_with_no_data():
    out = E.team_season_summaries({}, 2026, {})
    assert set(out) == set(E.TEAM_NAMES), "a listing needs a row per team, not per team with data"


# ------------------------------------------- division is published, not derived

def test_division_is_NOT_split_into_a_bare_region():
    """The source stores the conference inside the division. Publishing "West"
    would invent a decomposition it does not record."""
    src = open(os.path.join(E.ROOT, "jobs", "export_web.py"), encoding="utf-8").read()
    body = src.split("def load_team_groupings", 1)[1].split("\ndef ", 1)[0]
    assert ".split(" not in body, "the grouping loader must not decompose the source value"
    assert "team_division" in body and "team_conf" in body


def test_the_run_report_and_the_manifest_derive_rungs_from_ONE_source(db, monkeypatch):
    """`summary["counts"]` claims to mirror the manifest exactly.

    It used to derive `rungs` independently - its own disk read, its own call -
    so the claim was an intention. Two derivations of one number in one function
    is how a run report comes to disagree with the file it just wrote, and
    nothing asserted they matched. This does.
    """
    dest = str(db / "out")
    monkeypatch.setattr(config, "WEB_EXPORT_DIR", dest)
    summary = E.export(only=["players", "manifest"], now_ts=NOW, dest=dest)
    manifest = _walk(dest)["nfl/manifest.json"]
    assert summary["counts"]["rungs"] == manifest["counts"]["rungs"]
    assert summary["counts"]["market"] == manifest["counts"]["market"]
    assert summary["counts"]["players"] == manifest["counts"]["players"]


def test_a_run_without_the_market_part_still_resolves_its_counts(db, monkeypatch):
    """The hoisted block runs on EVERY path, so the `if "market" in parts` guard
    is load-bearing: `market` is never bound on a run that did not build it, and
    an unguarded reference would be a NameError in every export at once."""
    dest = str(db / "out")
    monkeypatch.setattr(config, "WEB_EXPORT_DIR", dest)
    summary = E.export(only=["teams"], now_ts=NOW, dest=dest)
    assert summary["counts"]["rungs"] == 0, "no market part, nothing on disk yet"
    assert "teams" in summary


def test_the_contract_carries_the_teams_ref_rather_than_an_inline_shape():
    with open(os.path.join(E.ROOT, "web", "contract", "v2", "contract.schema.json"),
              encoding="utf-8") as f:
        contract = json.load(f)
    teams = contract["$defs"]["SportManifest"]["properties"]["teams"]
    assert teams["items"] == {"$ref": "#/$defs/TeamEntry"}
    assert "memberships" in contract["$defs"]["TeamFile"]["required"]


# ------------------------------------------------ the season path (a-08)
#
# The teams board's chart: per team, per week, the running record and points,
# with played / bye / unplayed / gap as distinct states. The rule deciding the
# state is the team page schedule strip's (TeamView.tsx `slots()` over
# lib/slotState.ts), ported - so the tests below pin THAT rule's cases, and one
# test re-derives the strip's own forms from the TEAM FILE's schedule, which is
# what the strip actually reads, and requires the manifest to agree.

def test_the_summary_now_REQUIRES_the_path():
    bad = summary()
    bad.pop("cumulative")
    assert list(validator("TeamSeasonSummary").iter_errors(bad))


def test_a_played_week_must_carry_values_and_an_empty_week_must_carry_none():
    v = validator("TeamSeasonSummary")
    assert not list(v.iter_errors(summary()))
    # the other answers: a played week with a null, a bye with a zero, a state
    # the vocabulary does not have, and "played" wearing an empty week's nulls
    assert list(v.iter_errors(summary(cumulative=[{**played_week(1, 1, 0, 0, 36, 31),
                                                   "points_for": None}])))
    assert list(v.iter_errors(summary(cumulative=[{**empty_week(1, "bye"), "cleared": 0}])))
    assert list(v.iter_errors(summary(cumulative=[empty_week(1, "projected")])))
    assert list(v.iter_errors(summary(cumulative=[empty_week(1, "played")])))


def _sched(abbr, weeks, played=(), season=2026, opp="MIA"):
    """REG fixtures for `abbr` in `weeks`; those in `played` carry a 24-20 win."""
    out = {}
    for wk in weeks:
        hs, as_ = (24, 20) if wk in played else (None, None)
        out[f"{season}_{wk}_{abbr}"] = _game(season, wk, abbr, opp, hs, as_)
    return out


def _states(games, abbr, season=2026):
    return [e["state"] for e in E.team_season_summaries(games, season, {})[abbr]["cumulative"]]


def test_one_missing_week_IS_the_bye():
    games = _sched("BUF", [1, 2, 4, 5], played=[1, 2])
    assert _states(games, "BUF") == ["played", "played", "bye", "unplayed", "unplayed"]


def test_TWO_missing_weeks_leave_the_bye_unresolved_and_both_are_gaps():
    """A cancelled game never made up looks exactly like a bye. The strip does
    not guess which of two empty weeks was the bye, so neither does the path."""
    games = _sched("BUF", [1, 3, 5], played=[1, 3, 5])
    assert _states(games, "BUF") == ["played", "gap", "played", "gap", "played"]


def test_weeks_past_a_teams_last_fixture_are_gaps_and_do_not_count_toward_the_bye():
    """The strip counts gaps only up to the team's own last scheduled week."""
    games = {**_sched("BUF", [1, 2, 3, 4, 5]), **_sched("NE", [1, 3], played=[1], opp="NYJ")}
    # NE: week 2 is the single gap up to its last fixture (3), so it is the bye;
    # weeks 4 and 5 lie beyond it and are gaps, not a second candidate bye.
    assert _states(games, "NE") == ["played", "bye", "unplayed", "gap", "gap"]


def test_the_frame_is_the_leagues_schedule_length_not_the_teams():
    games = {**_sched("BUF", range(1, 19)), **_sched("NE", [1], opp="NYJ")}
    assert E.season_frame(games, 2026) == 18
    assert len(_states(games, "NE")) == 18
    assert len(_states(games, "BUF")) == 18


def test_a_team_with_no_fixtures_is_all_gaps_not_all_byes():
    games = _sched("BUF", [1, 2, 3])
    assert _states(games, "NE") == ["gap", "gap", "gap"]


def test_values_are_RUNNING_totals_and_every_non_played_week_is_NULL_not_zero():
    games = {
        "a": _game(2026, 1, "BUF", "MIA", 24, 20),   # cleared  24-20
        "b": _game(2026, 2, "NE", "BUF", 30, 17),    # missed   17-30
        # week 3: BUF's bye
        "d": _game(2026, 4, "BUF", "NYJ", 21, 21),   # tied     21-21
        "e": _game(2026, 5, "BUF", "KC", None, None),
    }
    path = E.team_season_summaries(games, 2026, {})["BUF"]["cumulative"]
    assert [e["index"] for e in path] == [1, 2, 3, 4, 5]
    assert path[0] == played_week(1, 1, 0, 0, 24, 20)
    assert path[1] == played_week(2, 1, 1, 0, 41, 50)
    assert path[2] == empty_week(3, "bye"), "a bye is not carried forward, and is not zero"
    assert path[3] == played_week(4, 1, 1, 1, 62, 71)
    assert path[4] == empty_week(5, "unplayed"), "no projection into an unplayed week"


def test_the_last_played_week_IS_the_season_summary():
    games = {
        "a": _game(2026, 1, "BUF", "MIA", 24, 20),
        "b": _game(2026, 2, "NE", "BUF", 30, 17),
        "c": _game(2026, 4, "BUF", "NYJ", 21, 21),
        "d": _game(2026, 5, "MIA", "NE", 3, 10),
    }
    checked = 0
    for abbr, s in E.team_season_summaries(games, 2026, {}).items():
        played = [e for e in s["cumulative"] if e["state"] == "played"]
        if not played:
            assert s["games"] == 0, abbr
            continue
        last = played[-1]
        assert (last["cleared"], last["missed"], last["tied"]) == (s["cleared"], s["missed"], s["tied"])
        assert (last["points_for"], last["points_against"]) == (s["points_for"], s["points_against"])
        checked += 1
    assert checked == 4, "BUF, NE, MIA, NYJ played; count must not be vacuous"


def test_the_export_REFUSES_a_path_that_disagrees_with_its_summary():
    """A played REG game with no week counts toward the season total and lands
    on no week of the path. The two would disagree on the page; refuse."""
    games = {"a": _game(2026, 1, "BUF", "MIA", 24, 20),
             "b": {**_game(2026, 2, "BUF", "NYJ", 10, 3), "week": None}}
    with pytest.raises(ValueError, match="BUF: season path ends at"):
        E.team_season_summaries(games, 2026, {})


def test_reconcile_discriminates():
    s = E.team_season_summaries({"a": _game(2026, 1, "BUF", "MIA", 24, 20)}, 2026, {})["BUF"]
    E.reconcile_path("BUF", s)                      # agrees: silent
    with pytest.raises(ValueError):
        E.reconcile_path("BUF", {**s, "points_for": 25})


def _strip_forms(schedule, season, weeks):
    """The web strip's `slots()` form per REG week, transcribed from
    TeamView.tsx on calibratedsports-web origin/main (9884f96), reading ONLY
    what the strip reads: the team file's schedule. `off` and `live` are one
    exported state (`unplayed`), so both map there."""
    reg = {g["index"]: g for g in schedule if g["season"] == season and g["game_type"] == "REG"}
    scheduled = list(reg)
    up_to = min(weeks, max(scheduled)) if scheduled else 0
    gaps = [wk for wk in range(1, up_to + 1) if wk not in reg]
    bye = gaps[0] if len(gaps) == 1 else None
    out = []
    for wk in range(1, weeks + 1):
        g = reg.get(wk)
        if g is not None and g["result"] is not None:
            out.append("played")
        elif bye == wk:
            out.append("bye")
        elif g is None:
            out.append("gap")
        else:
            out.append("unplayed")
    return out


def test_the_manifest_path_agrees_with_the_STRIP_read_off_the_team_file(db, monkeypatch):
    """The chart and the strip must not disagree about which weeks were played.
    The strip reads `schedule` on the team file; the path is built from the
    games table. Export both and compare every team, on the real exporter."""
    dest = str(db / "out")
    monkeypatch.setattr(config, "WEB_EXPORT_DIR", dest)
    E.export(only=["teams", "manifest"], now_ts=NOW, dest=dest)
    files = _walk(dest)
    manifest = files["nfl/manifest.json"]
    season = manifest["current"]["season"]
    compared = 0
    for t in manifest["teams"]:
        path = t["season"]["cumulative"]
        team = files[f"nfl/teams/{t['slug']}.json"]
        assert [e["state"] for e in path] == _strip_forms(team["schedule"], season, len(path)), t["abbr"]
        compared += 1
    assert compared == len(E.TEAM_NAMES)
    # the fixture must exercise more than one state, or agreement is vacuous
    states = {e["state"] for t in manifest["teams"] for e in t["season"]["cumulative"]}
    assert {"played", "unplayed", "gap", "bye"} <= states, states
