"""The web contract is executable, and the export is held to it.

web/contract/v2/contract.schema.json is the single source of truth shared with
calibratedsports-web, which generates its TypeScript types from the same file.
These tests cover the producer half: the contract is a legal schema, the export
matches it, the key table and version are DERIVED from it rather than copied,
and the two reported exclusions stay reported.

Why this file exists: both sides used to be independent transcriptions of
docs/web-schema.md, and they disagreed. identity.name was typed `string` on the
site while the exporter emitted null - a 500 on the player page, and a
browser-side throw that took out the whole players index behind an HTTP 200.
"""
import json
import os
import re
import sqlite3

import pytest
from jsonschema import Draft202012Validator

import config
import store
from jobs import export_web as E

from tests.test_export_web import NOW, db  # noqa: F401  (shared fixture)


# ------------------------------------------------------------------ the document

def test_the_contract_is_a_legal_2020_12_schema():
    Draft202012Validator.check_schema(E.CONTRACT)


def test_every_kind_names_a_def_that_exists_and_every_def_is_reachable():
    kinds = E.CONTRACT["x-contract"]["kinds"]
    defs = E.CONTRACT["$defs"]
    for kind, name in kinds.items():
        assert name in defs, f"{kind} -> missing $def {name}"
    # Every $def is either a kind's root or referenced by one; an orphan means a
    # type the site would generate and nothing would ever produce.
    refs = set(re.findall(r'"#/\$defs/([A-Za-z0-9_]+)"', json.dumps(E.CONTRACT)))
    orphans = set(defs) - refs - set(kinds.values())
    assert not orphans, f"unreferenced $defs: {sorted(orphans)}"


def test_the_contract_names_no_sport():
    """Sport-specific values live in a sport's manifest or the site's config."""
    text = json.dumps(E.CONTRACT).lower()
    for token in ("nfl", "mlb", "nba", "ncaa"):
        assert token not in text, f"the contract names a sport: {token}"


def test_closed_objects_are_the_default():
    """An ADDITIVE field must fail validation until the contract is updated in
    the same commit - that is what forces the site's types to regenerate."""
    for name, schema in E.CONTRACT["$defs"].items():
        if schema.get("type") == "object" and "properties" in schema:
            assert schema.get("additionalProperties") is False, f"{name} is open"


# ------------------------------------------------------------------ derived, not copied

def test_the_key_table_and_version_come_from_the_contract():
    assert E.SCHEMA_VERSION == E.CONTRACT["x-contract"]["schema_version"]
    assert len(E.KIND_BY_KEY) == len(E.CONTRACT["x-contract"]["keys"])
    for (rx, kind, sport), entry in zip(E.KIND_BY_KEY, E.CONTRACT["x-contract"]["keys"]):
        assert kind == entry["kind"]
        assert rx.pattern == entry["pattern"]
        sportless = kind in E.CONTRACT["x-contract"]["sportless_kinds"]
        assert sport == (None if sportless else "sport")


def test_export_web_holds_no_second_copy_of_the_key_patterns():
    """A literal key regex in the module would be a drift surface again."""
    src = open(os.path.join(E.ROOT, "jobs", "export_web.py"), encoding="utf-8").read()
    body = src.split("KIND_BY_KEY", 1)[1]
    assert "players/index" not in body.split("def ", 1)[0]


@pytest.mark.parametrize("key,kind", [
    ("sports.json", "sports"),
    ("nfl/manifest.json", "sport_manifest"),
    ("nfl/players/index.json", "player_index"),
    ("nfl/players/00-A/summary.json", "player_summary"),
    ("nfl/players/00-A/2025.json", "player_season"),
    ("nfl/teams/buf.json", "team"),
    ("nfl/market/00-A/2026-2.json", "market"),
    ("research/hypotheses.json", "research.hypotheses"),
])
def test_keys_route_to_their_kind(key, kind):
    assert E.kind_for_key(key)[0] == kind


def test_an_unknown_key_routes_nowhere():
    assert E.kind_for_key("nfl/players/00-A/notes.json")[0] is None


# ------------------------------------------------------------------ the export obeys it

def test_every_exported_file_validates_against_the_contract(db):
    dest = str(db / "out")
    E.export(only=["players", "teams", "manifest"], now_ts=NOW, dest=dest)
    files = {k: json.load(open(p, encoding="utf-8")) for k, p in E.local_keys(dest).items()}
    assert files
    E.validate_contract(files)          # raises ContractError with the detail


def test_a_file_that_drifts_from_the_contract_is_refused(db):
    dest = str(db / "out")
    E.export(only=["manifest"], now_ts=NOW, dest=dest)
    good = json.load(open(E.local_path(dest, "nfl/manifest.json"), encoding="utf-8"))

    # an added field. This is the headshot_url case: additive, harmless-looking,
    # and refused on purpose until the contract is updated in the same commit.
    with pytest.raises(E.ContractError, match="(?i)additional propert"):
        E.validate_contract({"nfl/manifest.json": dict(good, surprise=1)})
    # a re-typed field
    with pytest.raises(E.ContractError, match="period_type"):
        E.validate_contract({"nfl/manifest.json": dict(good, period_type=7)})
    # a dropped field
    E.validate_contract({"nfl/manifest.json": good})
    dropped = {k: v for k, v in good.items() if k != "counts"}
    with pytest.raises(E.ContractError, match="counts"):
        E.validate_contract({"nfl/manifest.json": dropped})
    # a key nothing in the contract routes
    with pytest.raises(E.ContractError, match="key table"):
        E.validate_contract({"nfl/whatever.json": good})


def test_nothing_reaches_disk_unvalidated(db, monkeypatch):
    """The check lives in sync_keys, the one choke point, not at each caller."""
    seen = []
    monkeypatch.setattr(E, "validate_contract", lambda files, **kw: seen.append(set(files)))
    dest = str(db / "out")
    E.export(only=["players", "teams", "manifest"], now_ts=NOW, dest=dest)
    written = set().union(*seen)
    assert "nfl/players/index.json" in written and "nfl/manifest.json" in written
    assert "sports.json" in written and "nfl/teams/buf.json" in written


# ------------------------------------------------------------------ the two exclusions

@pytest.fixture
def nameless(db):
    """A player with usage but no name anywhere: no xwalk row, null player_name."""
    c = sqlite3.connect(config.DB_PATH)
    c.execute("INSERT INTO nfl_player_week (gsis_id, season, week, season_type, data_version, "
              "player_name, position, team, opponent, receptions, targets, receiving_yards, "
              "receiving_tds, carries, rushing_yards, rushing_tds, attempts, fantasy_points_ppr, "
              "source, ingested_ts) VALUES ('00-NONAME',2026,1,'REG','v1',NULL,NULL,'BUF','HOU',"
              "1,2,8,0,0,0,0,0,1.8,'t',0)")
    c.commit()
    c.close()
    return db


def test_a_player_with_no_resolvable_name_is_excluded_and_counted(nameless):
    dest = str(nameless / "out")
    lines = []
    s = E.export(only=["players", "manifest"], now_ts=NOW, dest=dest, log=lines.append)

    assert s["excluded_no_name"] == {"count": 1, "ids": ["00-NONAME"]}
    assert any("excluded 1 player(s) with no resolvable name" in ln for ln in lines)

    files = {k: json.load(open(p, encoding="utf-8")) for k, p in E.local_keys(dest).items()}
    idx = files["nfl/players/index.json"]["players"]
    assert "00-NONAME" not in {p["id"] for p in idx}
    assert "nfl/players/00-NONAME/summary.json" not in files

    # Excluded, not disappeared: published in the manifest so it is visible.
    unresolved = files["nfl/manifest.json"]["unresolved_ids"]
    assert {"id": "00-NONAME", "name": None, "reason": E.REASON_NO_NAME} in unresolved
    assert files["nfl/manifest.json"]["counts"]["players"] == len(idx)


def test_the_count_is_reported_even_when_it_is_zero(db):
    """A number that prints 0 most weeks is what makes the week it prints 1 visible."""
    lines = []
    s = E.export(only=["players"], now_ts=NOW, dest=str(db / "out"), log=lines.append)
    assert s["excluded_no_name"] == {"count": 0, "ids": []}
    assert any("excluded 0 player(s) with no resolvable name" in ln for ln in lines)


def test_an_excluded_player_never_enters_the_permanent_slug_registry(nameless):
    dest = str(nameless / "out")
    E.export(only=["players"], now_ts=NOW, dest=dest)
    assert "00-NONAME" not in E.load_slug_registry(E.slug_registry_path())


def test_a_period_row_with_no_team_is_kept_but_left_out_of_the_display_list(db):
    c = sqlite3.connect(config.DB_PATH)
    c.execute("UPDATE nfl_player_week SET team = NULL WHERE gsis_id = '00-A' AND season = 2025 "
              "AND week = 1")
    c.commit()
    c.close()
    dest = str(db / "out")
    lines = []
    s = E.export(only=["players", "manifest"], now_ts=NOW, dest=dest, log=lines.append)

    assert s["rows_without_team"] == 1
    assert any("1 player(s) had a period row with no team" in ln for ln in lines)

    files = {k: json.load(open(p, encoding="utf-8")) for k, p in E.local_keys(dest).items()}
    summary = files["nfl/players/00-A/summary.json"]
    teams_2025 = [s_["teams"] for s_ in summary["seasons"] if s_["season"] == 2025][0]
    assert None not in teams_2025                       # the display list is clean
    row = files["nfl/players/00-A/2025.json"]["periods"][0]
    assert row["team"] is None                          # the record stays honest
    assert {"id": "00-A", "name": "Wide One", "reason": E.REASON_NO_TEAM} in \
        files["nfl/manifest.json"]["unresolved_ids"]
