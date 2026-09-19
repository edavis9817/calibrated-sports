"""Contract v2: the web export honours docs/web-schema.md."""
import ast
import io
import json
import os
import re
import sqlite3

import pytest

import config
import store
from jobs import export_web as E

NOW = 1_789_500_000.0  # 2026-09-15
V2_KEYS = ("WEB_EXPORT_DIR", "WEB_R2_BUCKET", "WEB_R2_ACCESS_KEY_ID", "WEB_R2_SECRET_ACCESS_KEY",
           "WEB_SITE_URL")
ALLOWED_POINTS = {"points_for", "points_against", "points_allowed", "points"}


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setattr(E, "SLUG_DIR", str(tmp_path / "slugs"))   # never the committed registry
    store.init_db()
    c = sqlite3.connect(config.DB_PATH)
    games = [
        # game_id, season, week, type, gameday, kick, home, away, hs, as, spread, total, hc, ac
        ("2025_01_BUF_MIA", 2025, 1, "REG", "2025-09-07", NOW - 3e7, "MIA", "BUF", 20, 27, -2.5, 47.5, "McD", "Mc"),
        ("2025_19_BUF_KC", 2025, 19, "WC", "2026-01-11", NOW - 2e7, "KC", "BUF", 24, 27, 3.0, 50.5, "Reid", "McD"),
        ("2026_01_BUF_HOU", 2026, 1, "REG", "2026-09-13", NOW - 2e5, "HOU", "BUF", 31, 36, 1.5, 44.5, "Ryans", "McD"),
        ("2026_02_DET_BUF", 2026, 2, "REG", "2026-09-17", NOW + 2e5, "BUF", "DET", None, None, 3.0, 52.5, "McD", "Campbell"),
        ("2012_01_OAK_SD", 2012, 1, "REG", "2012-09-10", NOW - 4e8, "SD", "OAK", 14, 22, 1.0, 40.0, "A", "B"),
    ]
    for g in games:
        c.execute("INSERT INTO nfl_games (game_id, data_version, season, week, game_type, gameday, "
                  "kickoff_ts, home_team, away_team, home_score, away_score, spread_line, total_line, "
                  "home_coach, away_coach, source, ingested_ts) VALUES (?,'v1',?,?,?,?,?,?,?,?,?,?,?,?,?,'t',0)",
                  (g[0], *g[1:]))
    weeks = [
        # gsis, season, week, type, name, pos, team, opp, rec, tgt, ryd, rtd, car, rush, rushtd, att, ppr
        ("00-A", 2025, 1, "REG", "Wide One", "WR", "BUF", "MIA", 7, 9, 88, 1, 0, 0, 0, 0, 21.8),
        ("00-A", 2025, 19, "POST", "Wide One", "WR", "BUF", "KC", 4, 6, 40, 0, 0, 0, 0, 0, 8.0),
        ("00-A", 2026, 1, "REG", "Wide One", "WR", "BUF", "HOU", 5, 8, 60, 0, 1, 5, 0, 0, 11.5),
        # fumbles_lost / two_pt_conversions / return_tds are set on this row via
        # UPDATE below: they were mapped 2026-09-16 and the fixture must prove
        # they FLOW THROUGH, not merely that the export tolerates them.
        ("00-D", 2026, 1, "REG", "Line Backer", "LB", "BUF", "HOU", 0, 0, 0, 0, 0, 0, 0, 0, 0.0),
        ("00-S1", 2025, 1, "REG", "Josh Allen", "QB", "BUF", "MIA", 0, 0, 0, 0, 5, 30, 1, 30, 20.0),
        ("00-S2", 2026, 1, "REG", "Josh Allen", "LB", "BUF", "HOU", 1, 1, 5, 0, 0, 0, 0, 0, 1.5),
    ]
    for w in weeks:
        c.execute("INSERT INTO nfl_player_week (gsis_id, season, week, season_type, data_version, "
                  "player_name, position, team, opponent, receptions, targets, receiving_yards, "
                  "receiving_tds, carries, rushing_yards, rushing_tds, attempts, fantasy_points_ppr, "
                  "def_tackles_solo, source, ingested_ts) VALUES (?,?,?,?,'v1',?,?,?,?,?,?,?,?,?,?,?,?,?,3,'t',0)", w)
    c.execute("UPDATE nfl_player_week SET fumbles_lost=1, two_pt_conversions=2, return_tds=1 "
              "WHERE gsis_id='00-A' AND season=2026 AND week=1")
    c.execute("INSERT INTO player_xwalk (gsis_id, display_name, position, pfr_id) VALUES ('00-A','Wide One','WR','WideWi00')")
    c.execute("INSERT INTO player_alias (alias, gsis_id, source) VALUES ('w one', '00-A', 'short')")
    c.execute("INSERT INTO nfl_snap_counts (pfr_player_id, game_id, data_version, season, week, player, "
              "offense_snaps, offense_pct, source, ingested_ts) VALUES ('WideWi00','2026_01_BUF_HOU','v1',2026,1,'Wide One',54,0.87,'t',0)")
    c.commit()
    c.close()
    return tmp_path


def _walk(dest):
    return {k: json.load(open(p, encoding="utf-8")) for k, p in E.local_keys(dest).items()}


def _keys_in(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _keys_in(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _keys_in(v)


# ------------------------------------------------------------------ config

def test_config_has_no_defaults_for_the_web_settings():
    src = open(os.path.join(E.ROOT, "config.py"), encoding="utf-8").read()
    for key in V2_KEYS:
        assert re.search(rf'^{key} = os\.getenv\("{key}"\)', src, re.M), key
    assert not re.search(r"^WEB_DATA_DIR =", src, re.M)


def test_export_refuses_without_a_destination(monkeypatch):
    monkeypatch.setattr(config, "WEB_EXPORT_DIR", None)
    with pytest.raises(E.ConfigError):
        E.export(only=["manifest"])


# ------------------------------------------------------------------ pure pieces

def test_team_spread_is_from_the_teams_own_perspective():
    assert E.team_spread(3.0, home=True) == 3.0
    assert E.team_spread(3.0, home=False) == -3.0
    assert E.team_spread(None, home=True) is None


def test_period_labels_come_from_game_type():
    assert E.period_label("REG", 3) == "Week 3"
    assert E.period_label("WC", 19) == "Wild Card"
    assert E.period_label("SB", 22) == "Super Bowl"
    assert E.period_label(None, 20, "POST") == "Postseason week 20"


def _step(values, t0=1_000.0, every=600.0):
    return [(t0 + i * every, v) for i, v in enumerate(values)]


def _replay(points):
    """Reconstruct the step series a path implies: each point holds until the
    next one. If this differs from the input, the encoding lost something."""
    return points


def test_price_path_keeps_every_change_and_invents_nothing():
    """The series is a step function, so change-points are LOSSLESS - not an
    approximation that happens to be close."""
    rows = _step([0.50, 0.50, 0.50, 0.55, 0.55, 0.40, 0.40, 0.40])
    points, dropped = E.price_path(rows)

    assert dropped == 0
    assert [(p["ts"], p["p"]) for p in points] == [
        (1000.0, 0.50), (2800.0, 0.55), (4000.0, 0.40), (5200.0, 0.40)]
    # Every emitted point is a real observation. Nothing between them exists.
    assert all((p["ts"], p["p"]) in rows for p in points)


def test_price_path_anchors_the_span_with_first_and_last():
    rows = _step([0.5, 0.5, 0.5, 0.5])
    points, dropped = E.price_path(rows)
    assert [p["ts"] for p in points] == [1000.0, 2800.0]   # first and last, flat between
    assert dropped == 0


def test_price_path_caps_by_dropping_the_smallest_wobbles_never_interpolating():
    """A volatile game-day market must stay bounded. Omitting a 1c wobble is
    honest; inventing a point is not."""
    # 40 alternating 1c wobbles, then three large moves that must survive.
    values = []
    for i in range(40):
        values.append(0.50 + (0.01 if i % 2 else 0.0))
    values += [0.80, 0.20, 0.90]
    rows = _step(values)
    points, dropped = E.price_path(rows, cap=10)

    assert len(points) == 10 and dropped > 0
    assert all((p["ts"], p["p"]) in rows for p in points)      # still no invention
    assert points[0]["ts"] == rows[0][0]                       # first anchored
    assert points[-1]["ts"] == rows[-1][0]                     # last anchored
    # The big moves outrank the 1c wobbles.
    assert {0.80, 0.20, 0.90} <= {p["p"] for p in points}


def test_price_path_handles_nothing_and_one_quote():
    assert E.price_path([]) == (None, 0)
    points, dropped = E.price_path(_step([0.42]))
    assert [p["p"] for p in points] == [0.42] and dropped == 0


def test_distribution_summary_shape():
    d = E.distribution_summary(list(range(0, 40)))
    assert len(d["cdf"]) == 51 and d["cdf"][0] == {"x": 0, "p_at_most": 0.025}
    assert [t["points"] for t in d["thresholds"]] == [5, 10, 15, 20, 25, 30]
    assert set(d["quantiles"]) == {"q10", "q25", "q50", "q75", "q90"}


def test_stale_detection_uses_periods():
    games = {"a": {"season": 2026, "week": 1, "home_score": 20, "game_type": "REG"},
             "b": {"season": 2026, "week": 2, "home_score": None, "game_type": "REG"}}
    fresh = E.current_period(games, [{"season": 2026, "week": 1}], NOW)
    assert fresh["period"] == {"index": 2, "label": "Week 2", "key": "2026-2"} and not fresh["stale"]
    assert fresh["data_through"] == {"season": 2026, "index": 1}
    games["b"]["home_score"] = 10
    games["c"] = {"season": 2026, "week": 3, "home_score": None, "game_type": "REG"}
    late = E.current_period(games, [{"season": 2026, "week": 1}], NOW)
    assert late["stale"] and "week 2" in late["stale_reason"]


def test_slugify_folds_to_ascii():
    assert E.slugify("Luka Dončić") == "luka-doncic"
    assert E.slugify("Amon-Ra St. Brown") == "amon-ra-st-brown"
    assert E.slugify("D'Andre Swift") == "d-andre-swift"


def _e(name, first, games):
    return {"name": name, "first_season": first, "reg_games": games}


def test_seed_gives_the_bare_slug_to_the_most_career_games():
    # the planted Adrian Peterson pair: the earlier player has few games
    reg, added = E.assign_slugs({"00-0021111": _e("Adrian Peterson", 2002, 60),
                                 "00-0025394": _e("Adrian Peterson", 2007, 184)})
    assert reg["00-0025394"] == "adrian-peterson"
    assert reg["00-0021111"] == "adrian-peterson-2002"
    assert set(added) == {"00-0021111", "00-0025394"}


def test_seed_ties_go_to_earliest_first_season_then_lowest_id_then_suffixes():
    reg, _ = E.assign_slugs({"00-0001111": _e("Josh Allen", 2018, 50),
                             "00-0002222": _e("Josh Allen", 2019, 50),
                             "00-0003333": _e("Josh Allen", 2019, 10)})
    assert reg["00-0001111"] == "josh-allen"
    assert reg["00-0002222"] == "josh-allen-2019"
    assert reg["00-0003333"] == "josh-allen-3333"


def test_existing_entries_are_immutable_when_a_newcomer_has_more_games():
    reg1, _ = E.assign_slugs({"00-0001111": _e("Josh Allen", 2018, 20),
                              "00-0009999": _e("Wide One", 2020, 5)})
    entries = {"00-0001111": _e("Josh Allen (renamed)", 2018, 20),      # name change too
               "00-0009999": _e("Wide One", 2020, 5),
               "00-0004444": _e("Josh Allen", 2026, 300)}
    reg2, added = E.assign_slugs(entries, reg1)
    assert all(reg2[k] == v for k, v in reg1.items())
    assert added == {"00-0004444": "josh-allen-2026"}


def test_newcomer_takes_the_bare_slug_only_if_free():
    reg, added = E.assign_slugs({"00-0007777": _e("New Guy", 2026, 1)}, {"00-0001111": "josh-allen"})
    assert added == {"00-0007777": "new-guy"}


def test_no_duplicate_slugs_even_when_a_name_looks_like_a_suffix():
    reg, _ = E.assign_slugs({"00-0001111": _e("Josh Allen", 2018, 90),
                             "00-0002222": _e("Josh Allen", 2019, 10),
                             "00-0005555": _e("Josh Allen 2019", 2020, 3)})
    assert len(set(reg.values())) == len(reg)
    assert reg["00-0005555"] == "josh-allen-2019"
    assert reg["00-0002222"] == "josh-allen-2222"


def test_registry_with_a_duplicate_slug_raises(tmp_path):
    with pytest.raises(E.SlugRegistryError):
        E.check_registry({"00-A": "same", "00-B": "same"})
    path = tmp_path / "nfl.json"
    path.write_text('{"00-A": "same", "00-B": "same"}', encoding="utf-8")
    with pytest.raises(E.SlugRegistryError):
        E.load_slug_registry(str(path))


def test_registry_written_to_disk_reloads_identically_one_entry_per_line(tmp_path):
    reg, _ = E.assign_slugs({"00-0025394": _e("Adrian Peterson", 2007, 184),
                             "00-0021111": _e("Adrian Peterson", 2002, 60),
                             "00-0036223": _e("Jonathan Taylor", 2020, 85)})
    path = str(tmp_path / "slugs" / "nfl.json")
    E.write_slug_registry(path, reg)
    assert E.load_slug_registry(path) == reg
    lines = open(path, encoding="utf-8").read().splitlines()
    assert lines[0] == "{" and lines[-1] == "}" and len(lines) == len(reg) + 2
    ids = [json.loads("{" + ln.rstrip(",") + "}").popitem()[0] for ln in lines[1:-1]]
    assert ids == sorted(ids)


def test_undefined_stat_keys_are_refused():
    bad = {"nfl/players/x/2025.json": {"kind": "player_season",
                                       "periods": [{"stats": {"rec": 1, "mystery": 2}}]}}
    with pytest.raises(E.StatDefinitionError, match="mystery"):
        E.assert_stats_defined(bad, E.STAT_DEFINITIONS)


def test_cache_control_per_contract():
    assert E.cache_control("sports.json") == "public, max-age=60"
    assert E.cache_control("nfl/manifest.json") == "public, max-age=60"
    assert E.cache_control("nfl/players/index.json") == "public, max-age=60"
    assert E.cache_control("nfl/players/00-A/summary.json") == "public, max-age=300"


def test_presets_only_reference_defined_stats():
    E.assert_stats_defined({"nfl/manifest.json": {"kind": "sport_manifest",
                                                  "scoring_presets": E.SCORING_PRESETS}},
                           E.STAT_DEFINITIONS)


# ------------------------------------------------------------------ export

def test_every_key_carries_the_v2_envelope_and_its_kind(db):
    dest = str(db / "out")
    E.export(only=["players", "teams", "manifest"], now_ts=NOW, dest=dest)
    files = _walk(dest)
    assert "sports.json" in files and "nfl/manifest.json" in files and "nfl/players/index.json" in files
    for key, obj in files.items():
        kind, sport_rule = E.kind_for_key(key)
        assert kind is not None, key
        assert obj["schema_version"] == 2 and obj["generated_at"], key
        assert obj["kind"] == kind, key
        assert obj["sport"] == ("nfl" if sport_rule == "sport" else None), key


def test_stat_definitions_cover_every_key_used_and_live_only_in_the_manifest(db):
    dest = str(db / "out")
    E.export(only=["players", "teams", "manifest"], now_ts=NOW, dest=dest)
    files = _walk(dest)
    defs = files["nfl/manifest.json"]["stat_definitions"]
    E.assert_stats_defined(files, defs)
    for key, obj in files.items():
        if key != "nfl/manifest.json":
            assert "stat_definitions" not in obj, key


def test_no_fantasy_points_anywhere_in_player_or_team_files(db):
    dest = str(db / "out")
    E.export(only=["players", "teams", "manifest"], now_ts=NOW, dest=dest)
    for key, obj in _walk(dest).items():
        if obj["kind"] not in ("player_summary", "player_season", "player_index", "team"):
            continue
        for k in _keys_in(obj):
            low = k.lower()
            assert "fantasy" not in low, (key, k)
            assert "points" not in low or k in ALLOWED_POINTS, (key, k)


def test_game_logs_are_split_by_season_and_summary_points_at_them(db):
    dest = str(db / "out")
    E.export(only=["players", "teams", "manifest"], now_ts=NOW, dest=dest)
    files = _walk(dest)
    summary = files["nfl/players/00-A/summary.json"]
    assert [s["season"] for s in summary["seasons"]] == [2025, 2026]
    for s in summary["seasons"]:
        season_file = files[s["key"]]
        assert season_file["kind"] == "player_season" and season_file["season"] == s["season"]
        assert {p["season"] for p in season_file["periods"]} == {s["season"]}
        assert len(season_file["periods"]) == s["games"]
    p25 = files["nfl/players/00-A/2025.json"]["periods"]
    assert [p["label"] for p in p25] == ["Week 1", "Wild Card"]
    p26 = files["nfl/players/00-A/2026.json"]["periods"][0]
    assert p26["stats"]["snap_share"] == 0.87 and p26["home"] is False and p26["opponent"] == "HOU"
    # Mapped from nflverse 2026-09-16. These were published as NULL since v1
    # under the note "not projected by nfl_player_week" - true of the table, and
    # the wrong conclusion. Proof the values reach the file, not just the schema.
    assert p26["stats"]["fum_lost"] == 1
    assert p26["stats"]["two_pt"] == 2
    assert p26["stats"]["ret_td"] == 1
    assert "games" not in summary and "periods" not in summary      # no logs on first paint
    assert summary["identity"]["slug"] == "wide-one" and summary["identity"]["aliases"] == ["w one"]


def test_index_scope_and_slugs(db):
    dest = str(db / "out")
    s = E.export(only=["players", "manifest"], now_ts=NOW, dest=dest)
    idx = _walk(dest)["nfl/players/index.json"]["players"]
    by_id = {p["id"]: p for p in idx}
    assert "00-D" not in by_id                            # no offensive usage
    assert by_id["00-S1"]["slug"] == "josh-allen"        # tie on games -> earliest first_season
    assert by_id["00-S2"]["slug"] == "josh-allen-2026"
    assert set(s["slug_collisions"]) == {"00-S2"}
    reg = E.load_slug_registry(E.slug_registry_path())   # seeded by the export
    assert reg["00-S1"] == "josh-allen" and s["slugs_added"] == len(reg)
    s2 = E.export(only=["players", "manifest"], now_ts=NOW, dest=dest)
    assert s2["slugs_added"] == 0 and E.load_slug_registry(E.slug_registry_path()) == reg


def test_team_keys_slugs_and_spread(db):
    dest = str(db / "out")
    E.export(only=["teams"], now_ts=NOW, dest=dest)
    files = _walk(dest)
    buf = files["nfl/teams/buf.json"]
    assert buf["identity"] == {"slug": "buf", "abbr": "BUF", "name": "Buffalo Bills"}
    at_hou = [s for s in buf["schedule"] if s["game_id"] == "2026_01_BUF_HOU"][0]
    assert at_hou["spread"] == -1.5 and at_hou["result"] == "W"
    assert at_hou["opponent"] == "hou" and at_hou["opponent_abbr"] == "HOU"
    assert "nfl/teams/lv.json" in files and "nfl/teams/oak.json" not in files
    assert any(s["game_id"] == "2012_01_OAK_SD" for s in files["nfl/teams/lv.json"]["schedule"])


def test_manifest_carries_period_type_presets_and_teams(db):
    dest = str(db / "out")
    E.export(only=["players", "manifest"], now_ts=NOW, dest=dest)
    m = _walk(dest)["nfl/manifest.json"]
    assert m["period_type"] == "week"
    assert m["current"]["period"] == {"index": 2, "label": "Week 2", "key": "2026-2"}
    assert set(m["scoring_presets"]) == {"ppr", "half", "standard"}
    assert m["scoring_presets"]["half"]["weights"]["rec"] == 0.5
    # THE ENTRY, NOT A LITERAL DICT. teams[] gained conference, division,
    # classification and a season summary (track B's A14, track C's C-3), so an
    # exact-match assertion encodes the OLD shape rather than the requirement -
    # the same way the upload test asserted a deletion that no longer needs no
    # declaration. Rewritten to the property, not deleted to go green.
    buf = next(t for t in m["teams"] if t["abbr"] == "BUF")
    assert (buf["slug"], buf["name"]) == ("buf", "Buffalo Bills")
    assert set(buf) == {"slug", "abbr", "name", "conference", "division",
                        "classification", "season"}
    # THIS FIXTURE INSERTS NO `nfl_teams` ROWS, so the grouping source is
    # ABSENT. Null is the only honest answer and the export must not invent one
    # - and note what that means about this test: it CANNOT tell a correct read
    # from a missing source, because both produce null. The real values are
    # exercised against the store, never here.
    assert buf["conference"] is None and buf["division"] is None
    assert buf["classification"] is None, "this sport has no competitive tier"
    # The season summary is present and internally consistent. `tied` exists
    # precisely so this identity holds on a drawn game.
    season = buf["season"]
    assert set(season) == {"games", "cleared", "missed", "tied",
                           "points_for", "points_against", "markets"}
    assert season["cleared"] + season["missed"] + season["tied"] == season["games"]


def test_counts_games_played_not_games_scheduled(db):
    """The fixture has five games and one of them has not been played.

    len(games) would report 5. The coverage figure must report 4: nfl_games
    carries scheduled rows with a null score, and counting them claims a record
    the site does not hold. 2026_02_DET_BUF is the unplayed one - it is also the
    current period, which is exactly when this is easiest to get wrong.
    """
    dest = str(db / "out")
    E.export(only=["players", "manifest"], now_ts=NOW, dest=dest)
    m = _walk(dest)["nfl/manifest.json"]
    assert m["counts"]["games"] == 4
    assert m["counts"]["teams"] == len(E.TEAM_NAMES)


def test_counts_rungs_is_zero_only_when_no_market_is_published(db):
    """rungs travels with the market count, so the two cannot disagree."""
    dest = str(db / "out")
    E.export(only=["players", "manifest"], now_ts=NOW, dest=dest)
    c = _walk(dest)["nfl/manifest.json"]["counts"]
    # No market part in this run and none on disk, so both read zero together.
    assert c["market"] == 0 and c["rungs"] == 0


def test_count_rungs_sums_every_components_ladder():
    files = {
        "a.json": {"components": [{"rungs": [1, 2, 3]}, {"rungs": []}, {"note": "no rungs key"}]},
        "b.json": {"components": [{"rungs": [1, 2]}]},
    }
    assert E.count_rungs(files) == 5
    assert E.count_rungs({}) == 0


def test_rewrites_only_on_change_and_deletes_stale_keys(db):
    dest = str(db / "out")
    E.export(only=["players", "teams", "manifest"], now_ts=NOW, dest=dest)
    paths = E.local_keys(dest)
    before = {p: os.path.getmtime(p) for p in paths.values()}
    s2 = E.export(only=["players", "teams", "manifest"], now_ts=NOW + 60, dest=dest)
    assert s2["players"] == (0, 0) and s2["teams"] == (0, 0) and s2["manifest"] == (0, 0)
    assert all(os.path.getmtime(p) == t for p, t in before.items())
    gone = os.path.join(dest, "nfl", "players", "00-GONE", "summary.json")
    os.makedirs(os.path.dirname(gone))
    open(gone, "w").write("{}")
    E.export(only=["players"], now_ts=NOW, dest=dest)
    assert not os.path.exists(gone) and not os.path.exists(os.path.dirname(gone))


# ------------------------------------------------------------------ upload

class FakeS3:
    """An honest double: it keeps what it was given and serves it back, so the
    upload record's round trip through the bucket is actually exercised."""

    def __init__(self, objects=None):
        self.puts, self.deletes = {}, []
        self.objects = dict(objects or {})      # key -> bytes already in the bucket
        self.listed = 0

    def put_object(self, Bucket, Key, Body, ContentType, CacheControl):
        self.puts[Key] = {"bucket": Bucket, "body": Body, "type": ContentType, "cache": CacheControl}
        self.objects[Key] = Body

    def delete_object(self, Bucket, Key):
        self.deletes.append(Key)
        self.objects.pop(Key, None)

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise KeyError(Key)                 # boto3 raises NoSuchKey; any raise is handled
        return {"Body": io.BytesIO(self.objects[Key])}

    def list_objects_v2(self, Bucket):
        self.listed += 1
        return {"Contents": [{"Key": k} for k in sorted(self.objects)]}


def _data_puts(s3):
    """Site data only - the upload record is bookkeeping, not content."""
    return {k: v for k, v in s3.puts.items() if k != E.REMOTE_STATE_KEY}


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setattr(config, "WEB_R2_ACCESS_KEY_ID", "id")
    monkeypatch.setattr(config, "WEB_R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setattr(config, "WEB_R2_BUCKET", "calibrated-sports-site")


def _seed(dest):
    for key in ("sports.json", "nfl/manifest.json", "nfl/players/00-A/summary.json"):
        E.write_if_changed(E.local_path(dest, key), {"kind": "x", "key": key})


def test_upload_sends_only_changed_keys_and_deletes_removed_ones(tmp_path, creds):
    dest = str(tmp_path / "exp")
    _seed(dest)
    s3 = FakeS3()
    r1 = E.upload(dest=dest, client=s3, log=lambda *_: None)
    assert r1["uploaded"] == 3 and r1["considered"] == 3 and r1["deleted"] == 0
    assert s3.puts["nfl/manifest.json"]["cache"] == "public, max-age=60"
    assert s3.puts["nfl/players/00-A/summary.json"]["cache"] == "public, max-age=300"
    assert s3.puts["sports.json"]["type"] == "application/json"
    assert E.STATE_FILE not in s3.puts and not any(k.endswith(E.STATE_FILE) for k in s3.puts)

    s3b = FakeS3()
    r2 = E.upload(dest=dest, client=s3b, log=lambda *_: None)
    # No SITE DATA is re-sent. The record itself is mirrored on every run,
    # including one that uploads nothing - one small PUT, and it keeps the
    # bucket's copy current against local edits.
    assert r2["changed"] == 0 and _data_puts(s3b) == {}
    assert list(s3b.puts) == [E.REMOTE_STATE_KEY]

    E.write_if_changed(E.local_path(dest, "nfl/manifest.json"), {"kind": "x", "changed": True})
    os.remove(E.local_path(dest, "nfl/players/00-A/summary.json"))
    s3c = FakeS3()
    # THE DECLARATION IS WHAT AUTHORISES THE DELETE, and this assertion used to
    # run without one. That was the old rule - absence alone meant "delete" - and
    # under it every key any OTHER producer writes computes as removed, because
    # local_keys() walks WEB_EXPORT_DIR and nothing else. The run now has to say
    # it rebuilt the prefix the key lives under.
    r3 = E.upload(dest=dest, client=s3c, log=lambda *_: None, refreshed=["nfl/players/"])
    assert list(_data_puts(s3c)) == ["nfl/manifest.json"]
    assert s3c.deletes == ["nfl/players/00-A/summary.json"] and r3["deleted"] == 1
    assert r3["removed_withheld"] == 0
    state = json.load(open(os.path.join(dest, E.STATE_FILE), encoding="utf-8"))
    assert "nfl/players/00-A/summary.json" not in state


def test_without_a_declaration_NOTHING_is_deleted(tmp_path, creds):
    """Absence is not information, and this is the defect it closes.

    `weekly_refresh` runs `--upload-only` unconditionally, so under the old rule
    track F's 88 analytics keys - written to their own root, invisible to
    `local_keys` - were a SCHEDULED deletion on the next ordinary Tuesday. Not a
    hazard someone might trigger: what the design did on its own.
    """
    dest = str(tmp_path / "exp")
    _seed(dest)
    first = FakeS3()
    E.upload(dest=dest, client=first, log=lambda *_: None)
    os.remove(E.local_path(dest, "nfl/players/00-A/summary.json"))

    s3 = FakeS3(objects=dict(first.objects))
    lines = []
    r = E.upload(dest=dest, client=s3, log=lines.append, refreshed=None)

    assert s3.deletes == [] and r["deleted"] == 0
    assert r["removed_withheld"] == 1 and r["declared_prefixes"] is None
    # The object survives in the bucket AND in the record, so a later run that
    # does declare the prefix can still remove it. Withholding defers; it does
    # not forget.
    assert "nfl/players/00-A/summary.json" in s3.objects
    state = json.load(open(os.path.join(dest, E.STATE_FILE), encoding="utf-8"))
    assert "nfl/players/00-A/summary.json" in state
    assert any("declared no refreshed prefixes" in ln for ln in lines)


def test_a_declaration_that_does_not_cover_the_key_withholds_it_and_warns(tmp_path, creds):
    """The signal case: keys vanished from outside every prefix the run rebuilt.

    In a full run this should be 0, so a climbing count is how a declaration
    that silently stops being threaded through announces itself.
    """
    dest = str(tmp_path / "exp")
    _seed(dest)
    first = FakeS3()
    E.upload(dest=dest, client=first, log=lambda *_: None)
    os.remove(E.local_path(dest, "nfl/players/00-A/summary.json"))

    s3 = FakeS3(objects=dict(first.objects))
    lines = []
    r = E.upload(dest=dest, client=s3, log=lines.append, refreshed=["nfl/teams/"])

    assert s3.deletes == [] and r["deleted"] == 0
    assert r["removed_withheld"] == 1 and r["declared_prefixes"] == ["nfl/teams/"]
    assert any("outside every declared prefix" in ln for ln in lines)


def test_none_and_empty_both_withhold_and_are_still_told_apart(tmp_path, creds):
    """They do the same thing and MEAN different things.

    None is "nobody said". [] is a manifest-only run declaring it rebuilt no
    prefix - the manifest `sync_keys` call passes [] on purpose. A reader
    looking at a non-zero `removed_withheld` needs to know which one it was
    before deciding whether the number is benign.
    """
    dest = str(tmp_path / "exp")
    _seed(dest)
    first = FakeS3()
    E.upload(dest=dest, client=first, log=lambda *_: None)
    os.remove(E.local_path(dest, "nfl/players/00-A/summary.json"))

    said_nothing = E.upload(dest=dest, client=FakeS3(objects=dict(first.objects)),
                            log=lambda *_: None, refreshed=None)
    declared_empty = E.upload(dest=dest, client=FakeS3(objects=dict(first.objects)),
                              log=lambda *_: None, refreshed=[])

    assert said_nothing["deleted"] == declared_empty["deleted"] == 0
    assert said_nothing["removed_withheld"] == declared_empty["removed_withheld"] == 1
    assert said_nothing["declared_prefixes"] is None
    assert declared_empty["declared_prefixes"] == []


def test_withheld_prefixes_NAMES_the_prefix_that_went_unclaimed(tmp_path, creds):
    """A count cannot say whose keys were withheld.

    With a second producer writing into this tree, `removed_withheld: 1` is
    either a benign undeclared run or a retired metric that will stay served
    from R2 forever. The prefix is what distinguishes them, and the job log has
    nothing else to name.
    """
    dest = str(tmp_path / "exp")
    _seed(dest)
    first = FakeS3()
    E.upload(dest=dest, client=first, log=lambda *_: None)

    # a key from ANOTHER producer, in the record and gone from disk
    state_path = os.path.join(dest, E.STATE_FILE)
    state = json.load(open(state_path, encoding="utf-8"))
    state["analytics/nfl/pace.plays_per_game.json"] = "whatever"
    json.dump(state, open(state_path, "w", encoding="utf-8"))

    s3 = FakeS3(objects=dict(first.objects))
    r = E.upload(dest=dest, client=s3, log=lambda *_: None,
                 refreshed=[f"{E.SPORT}/players/"])

    assert r["removed_withheld"] == 1
    assert r["withheld_prefixes"] == ["analytics/"]
    assert s3.deletes == [], "nothing outside the declared prefix may be deleted"


def test_withheld_prefixes_is_empty_when_nothing_is_withheld(tmp_path, creds):
    """The other answer on the other input."""
    dest = str(tmp_path / "exp")
    _seed(dest)
    s3 = FakeS3()
    r = E.upload(dest=dest, client=s3, log=lambda *_: None, refreshed=[f"{E.SPORT}/"])
    assert r["removed_withheld"] == 0
    assert r["withheld_prefixes"] == []


def test_the_upload_record_is_mirrored_into_the_bucket_but_is_not_site_data(tmp_path, creds):
    """Bookkeeping, not content: it must reach the bucket and must never be
    served as a data key."""
    dest = str(tmp_path / "exp")
    _seed(dest)
    s3 = FakeS3()
    E.upload(dest=dest, client=s3, log=lambda *_: None)

    assert E.REMOTE_STATE_KEY in s3.puts
    assert s3.puts[E.REMOTE_STATE_KEY]["cache"] == "no-store"
    assert json.loads(s3.objects[E.REMOTE_STATE_KEY]) == json.load(
        open(os.path.join(dest, E.STATE_FILE), encoding="utf-8"))
    # The LOCAL record's filename never appears as an uploaded key.
    assert not any(k.endswith(E.STATE_FILE) for k in s3.puts)


def test_a_machine_with_no_local_record_recovers_it_from_the_bucket(tmp_path, creds):
    """Losing the machine used to cost a full re-upload of every object. The
    record comes back from R2 instead - and nothing re-uploads."""
    dest = str(tmp_path / "exp")
    _seed(dest)
    first = FakeS3()
    E.upload(dest=dest, client=first, log=lambda *_: None)

    os.remove(os.path.join(dest, E.STATE_FILE))          # the machine is gone
    second = FakeS3(objects=dict(first.objects))         # the bucket is not
    r = E.upload(dest=dest, client=second, log=lambda *_: None)

    assert r["state_source"] == "r2"
    assert r["changed"] == 0 and r["uploaded"] == 0
    assert _data_puts(second) == {}


def test_a_recovered_record_is_reconciled_against_the_bucket(tmp_path, creds):
    """A record that claims a key the bucket does not have would silently skip
    uploading it. Anything absent is dropped, so it re-uploads."""
    dest = str(tmp_path / "exp")
    _seed(dest)
    first = FakeS3()
    E.upload(dest=dest, client=first, log=lambda *_: None)

    os.remove(os.path.join(dest, E.STATE_FILE))
    objects = dict(first.objects)
    objects.pop("nfl/manifest.json")                     # never actually landed
    second = FakeS3(objects=objects)
    r = E.upload(dest=dest, client=second, log=lambda *_: None)

    assert r["state_source"] == "r2"
    assert list(_data_puts(second)) == ["nfl/manifest.json"]


def test_an_unlistable_bucket_re_uploads_rather_than_trusting_the_record(tmp_path, creds):
    """Expensive and correct beats cheap and wrong: an unverified record would
    skip uploads that never happened."""
    dest = str(tmp_path / "exp")
    _seed(dest)
    first = FakeS3()
    E.upload(dest=dest, client=first, log=lambda *_: None)

    os.remove(os.path.join(dest, E.STATE_FILE))
    second = FakeS3(objects=dict(first.objects))
    second.list_objects_v2 = lambda **kw: (_ for _ in ()).throw(RuntimeError("no listing"))
    lines = []
    r = E.upload(dest=dest, client=second, log=lines.append)

    assert r["state_source"] == "unverified"
    assert sorted(_data_puts(second)) == sorted(E.local_keys(dest))
    assert any("re-uploading rather than trusting it" in ln for ln in lines)


def test_a_failed_mirror_does_not_fail_the_upload(tmp_path, creds):
    """The local record is authoritative for the machine that did the work; an
    upload must not fail because its bookkeeping could not be copied."""
    dest = str(tmp_path / "exp")
    _seed(dest)

    class NoMirror(FakeS3):
        def put_object(self, Bucket, Key, **kw):
            if Key == E.REMOTE_STATE_KEY:
                raise RuntimeError("mirror refused")
            return super().put_object(Bucket, Key, **kw)

    s3 = NoMirror()
    r = E.upload(dest=dest, client=s3, log=lambda *_: None)

    assert r["uploaded"] == 3
    assert os.path.exists(os.path.join(dest, E.STATE_FILE))


def test_upload_not_configured_is_logged_and_exits_zero(tmp_path, monkeypatch):
    dest = str(tmp_path / "exp")
    _seed(dest)
    monkeypatch.setattr(config, "WEB_EXPORT_DIR", dest)
    monkeypatch.setattr(config, "WEB_R2_ACCESS_KEY_ID", None)
    monkeypatch.setattr(config, "WEB_R2_SECRET_ACCESS_KEY", None)
    lines = []
    assert E.upload(dest=dest, log=lines.append) == {"configured": False}
    assert "R2 upload not configured" in lines[0]
    assert E.main(["--upload-only"]) == 0


# --------------------------------------------------- the declaration, end to end

def test_the_sentinel_the_export_PRINTS_is_the_one_the_parser_READS(db, monkeypatch, capsys):
    """END TO END, with no fake in between.

    The producer is the real `main()`, the consumer is the real
    `parse_refreshed()`, and the text between them is real stdout. Two fakes
    agreeing with each other is exactly the failure this excludes: a test
    asserting main() prints "REFRESHED ..." and separately that the parser reads
    "REFRESHED ..." would stay green while the two used different sentinels, and
    the only symptom in production is deletions quietly never happening.
    """
    dest = str(db / "exp")
    monkeypatch.setattr(config, "WEB_EXPORT_DIR", dest)

    # `--only`, not a whole run: `research` resolves a model version and this
    # fixture has no predictions, so a full export raises before it can print.
    # The part list is not what is under test - the path from main()'s stdout to
    # the parser is, and it is the REAL one in both directions.
    assert E.main(["--only", "players", "--only", "teams"]) == 0
    out = capsys.readouterr().out

    refreshed = E.parse_refreshed(out)
    assert refreshed is not None, f"the export printed no sentinel line:\n{out[-400:]}"
    assert set(refreshed) == {f"{E.SPORT}/players/", f"{E.SPORT}/teams/"}, refreshed
    # And it discriminates: a run that rebuilt the players prefix must not be
    # read as one that rebuilt everything.
    assert f"{E.SPORT}/market/" not in refreshed and "research/" not in refreshed


def test_every_sync_keys_PREFIX_is_also_DECLARED_to_the_uploader():
    """The two readers of the same literals must agree, checked statically.

    `sync_keys(dest, wanted, prefixes)` says which prefixes a builder OWNS;
    `refreshed.append(...)` says which it DECLARES to the uploader. They are
    written one line apart and nothing but habit keeps them in step. A prefix
    owned but not declared can never be deleted from R2 - keys the export
    stopped producing would accumulate forever, silently, which is the failure
    the declaration exists to make safe rather than to reintroduce. One declared
    but not owned would authorise deleting keys this builder does not make.

    Static, so it covers `research/` - which cannot be exported on a fixture
    with no predictions - and it fails on any literal it cannot evaluate rather
    than checking the subset it understood.
    """
    tree = ast.parse(open(os.path.join(E.ROOT, "jobs", "export_web.py"), encoding="utf-8").read())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "export")

    def literal(node):
        """A string from a Constant or an f-string over SPORT, else None."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            out = []
            for part in node.values:
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    out.append(part.value)
                elif (isinstance(part, ast.FormattedValue)
                        and isinstance(part.value, ast.Name) and part.value.id == "SPORT"):
                    out.append(E.SPORT)
                else:
                    return None
            return "".join(out)
        return None

    owned, declared, unresolved = set(), set(), 0
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name == "sync_keys":
            if len(node.args) < 3 or not isinstance(node.args[2], (ast.List, ast.Tuple)):
                unresolved += 1                      # prefixes built at runtime
                continue
            for element in node.args[2].elts:
                value = literal(element)
                if value is None:
                    unresolved += 1
                else:
                    owned.add(value)
        elif (name == "append" and isinstance(getattr(node.func, "value", None), ast.Name)
                and node.func.value.id == "refreshed" and node.args):
            value = literal(node.args[0])
            if value is None:
                unresolved += 1
            else:
                declared.add(value)

    assert unresolved == 0, f"{unresolved} literal(s) this guard cannot evaluate"
    assert owned, "the AST walk found no sync_keys prefixes - exit 0 is not a result"
    assert owned == declared, (
        f"sync_keys owns {sorted(owned)} but export() declares {sorted(declared)}; "
        f"owned-not-declared {sorted(owned - declared)} can never be deleted from R2, "
        f"declared-not-owned {sorted(declared - owned)} would delete another builder's keys")


def test_a_partial_run_declares_only_what_it_rebuilt(db, monkeypatch):
    dest = str(db / "exp")
    monkeypatch.setattr(config, "WEB_EXPORT_DIR", dest)
    assert E.export(dest=dest, now_ts=NOW, only=["teams"])["refreshed"] == [f"{E.SPORT}/teams/"]


def test_a_manifest_only_run_declares_EMPTY_not_none(db, monkeypatch):
    """The manifest `sync_keys` call passes [] on purpose - it owns no prefix.
    So the run declared, and owns nothing. `[]` and `None` reach `upload()` as
    different facts."""
    dest = str(db / "exp")
    monkeypatch.setattr(config, "WEB_EXPORT_DIR", dest)
    refreshed = E.export(dest=dest, now_ts=NOW, only=["manifest"])["refreshed"]
    assert refreshed == [] and refreshed is not None


@pytest.mark.parametrize("prefixes", [[], ["nfl/players/"], ["nfl/players/", "research/"]])
def test_the_declaration_round_trips_through_the_command_line(prefixes):
    """The wire format is a space-joined line, which is how weekly_refresh sends
    it. An empty declaration must survive as [] rather than collapsing to None."""
    assert E.parse_refreshed(E.REFRESHED_SENTINEL + " " + " ".join(prefixes)) == prefixes


def test_no_sentinel_parses_to_NONE_and_never_to_EMPTY():
    """The distinction the whole mechanism exists to preserve. If a failed parse
    returned [], "nobody said" would be indistinguishable from "declared, owns
    nothing" - and both withhold today, so the bug would be invisible until the
    day one of them was allowed to delete."""
    assert E.parse_refreshed("summary\nunresolved ids: 0") is None
    assert E.parse_refreshed("") is None
    assert E.parse_refreshed(None) is None
    assert E.parse_refreshed(E.REFRESHED_SENTINEL) == []
    assert E.parse_refreshed(E.REFRESHED_SENTINEL + " ") == []


def test_the_parser_takes_the_LAST_sentinel_line(db):
    """stdout carries a whole run's logging ahead of it, and a docstring or a log
    line could legitimately contain the word. The sentinel is printed last."""
    text = f"REFRESHED is the word\n{E.REFRESHED_SENTINEL} nfl/teams/"
    assert E.parse_refreshed(text) == ["nfl/teams/"]


def test_upload_only_with_no_flag_declares_nothing(tmp_path, monkeypatch, creds):
    """`--upload-only` without `--refreshed` must not invent a declaration."""
    dest = str(tmp_path / "exp")
    _seed(dest)
    monkeypatch.setattr(config, "WEB_EXPORT_DIR", dest)
    seen = {}
    monkeypatch.setattr(E, "upload", lambda **kw: seen.update(kw) or {"configured": False})
    assert E.main(["--upload-only"]) == 0
    assert seen["refreshed"] is None


def test_upload_only_with_an_empty_flag_declares_EMPTY(tmp_path, monkeypatch, creds):
    dest = str(tmp_path / "exp")
    _seed(dest)
    monkeypatch.setattr(config, "WEB_EXPORT_DIR", dest)
    seen = {}
    monkeypatch.setattr(E, "upload", lambda **kw: seen.update(kw) or {"configured": False})
    assert E.main(["--upload-only", "--refreshed", ""]) == 0
    assert seen["refreshed"] == []


def test_hypotheses_source_uses_only_the_contracts_verdicts():
    src = json.load(open(os.path.join(E.ROOT, "docs", "hypotheses.json"), encoding="utf-8"))
    assert src["hypotheses"]
    for h in src["hypotheses"]:
        assert h["verdict"] in {"retired", "null", "not_testable", "open"}
        assert h["interval"] is None or (len(h["interval"]) == 2 and h["interval"][0] <= h["interval"][1])
        assert os.path.exists(os.path.join(E.ROOT, h["script"])), h["script"]


# ============================================================================
# per-row key sets: a row carries what the player's production justifies
# ============================================================================
#
# THE GUARD Ethan asked for, over BUILT OUTPUT rather than over the pure
# function. `emitted_keys` having the right behaviour says nothing about the
# three call sites using it - the period build, played_zero_periods and
# _totals - and it was the call sites, not the rule, that manufactured zeros.

def _player_season_files(files):
    return {k: v for k, v in files.items()
            if "/players/" in k and "summary" not in k and "index" not in k}


def test_no_period_row_carries_a_key_that_is_zero_all_season(db):
    """The rule: a key zero in EVERY period of a file must not be in any row.

    Folding every stat the sport records into one key list puts eleven
    defensive zeros on a receiver's page and the receiving block on a
    linebacker's - and the played-zero path MANUFACTURES them, writing zeros
    that were never in the source.
    """
    dest = str(db / "out")
    E.export(only=["players", "teams", "manifest"], now_ts=NOW, dest=dest)
    offenders = []
    for key, doc in _player_season_files(_walk(dest)).items():
        periods = doc.get("periods") or []
        candidates = {k for p in periods for k in p["stats"]} - set(E.USAGE_KEYS)
        for stat in candidates:
            values = [p["stats"][stat] for p in periods if stat in p["stats"]]
            if values and all(v == 0 for v in values):
                offenders.append((key, stat))
    assert not offenders, f"keys zero in every period but still emitted: {offenders}"


def test_a_key_the_player_DOES_accumulate_survives(db):
    """Discriminating. Without this, an `emitted_keys` returning () always would
    satisfy the test above while deleting the whole game log."""
    dest = str(db / "out")
    E.export(only=["players", "teams", "manifest"], now_ts=NOW, dest=dest)
    files = _player_season_files(_walk(dest))

    wr = next(v for k, v in files.items() if "/00-A/" in k)
    wr_keys = {k for p in wr["periods"] for k in p["stats"]}
    assert "rec" in wr_keys, "the receiver lost his receptions"
    assert "pass_att" not in wr_keys, "the receiver kept a passing column of zeros"

    qb = next(v for k, v in files.items() if "/00-S1/" in k)
    qb_keys = {k for p in qb["periods"] for k in p["stats"]}
    assert "pass_att" in qb_keys, "the quarterback lost his attempts"
    assert "rec" not in qb_keys, "the quarterback kept a receptions column of zeros"


def test_usage_keys_survive_even_when_zero(db):
    """A played-zero row IS snaps > 0 with every stat zero. If the rule reached
    `snaps` it would delete the key proving he played, leaving a row that
    asserts nothing."""
    dest = str(db / "out")
    E.export(only=["players", "teams", "manifest"], now_ts=NOW, dest=dest)
    for key, doc in _player_season_files(_walk(dest)).items():
        for p in doc["periods"]:
            assert "snaps" in p["stats"], key
            assert "snap_share" in p["stats"], key


def test_the_key_set_is_per_SEASON_not_per_career(db):
    """The wiring bug the guard caught. `00-A` catches a touchdown in 2025 and
    runs the ball in 2026. Scoped to the career, his 2026 file would carry
    `rec_td: 0` and his 2025 file `rush_att: 0` - each a column of zeros in the
    season the reader is actually looking at."""
    dest = str(db / "out")
    E.export(only=["players", "teams", "manifest"], now_ts=NOW, dest=dest)
    files = _player_season_files(_walk(dest))
    y2025 = {k for p in next(v for k, v in files.items() if "/00-A/2025" in k)["periods"]
             for k in p["stats"]}
    y2026 = {k for p in next(v for k, v in files.items() if "/00-A/2026" in k)["periods"]
             for k in p["stats"]}
    assert "rec_td" in y2025 and "rec_td" not in y2026, "rec_td leaked across seasons"
    assert "rush_att" in y2026 and "rush_att" not in y2025, "rush_att leaked across seasons"


def test_the_CAREER_total_keeps_a_stat_from_any_single_season(db):
    """The converse, and the thing per-season scoping could plausibly break: a
    career line must still show a stat the player only ever accumulated once.
    `_totals` keeps any key some period carries, so the career call gets the
    union across seasons for free - asserted rather than assumed."""
    dest = str(db / "out")
    E.export(only=["players", "teams", "manifest"], now_ts=NOW, dest=dest)
    summary = next(v for k, v in _walk(dest).items() if k.endswith("00-A/summary.json"))
    career = summary["career"]["stats"]
    assert "rec_td" in career, "a 2025-only touchdown vanished from the career line"
    assert "rush_att" in career, "a 2026-only carry vanished from the career line"


def test_season_totals_do_not_carry_a_key_the_periods_dropped(db):
    """A file that contradicts itself is worse than a missing one - the
    `season_type: "REG"` lesson. Totalling the full COUNT_KEYS would put
    `pass_att: 0` in a receiver's season totals while his game log has no such
    column."""
    dest = str(db / "out")
    E.export(only=["players", "teams", "manifest"], now_ts=NOW, dest=dest)
    files = _walk(dest)
    for key, doc in files.items():
        if not key.endswith("summary.json"):
            continue
        gsis = key.split("/")[2]
        period_keys = set()
        for k2, d2 in _player_season_files(files).items():
            if f"/{gsis}/" in k2:
                period_keys |= {k for p in d2["periods"] for k in p["stats"]}
        for total in doc.get("season_totals", []):
            extra = set(total["stats"]) - period_keys - {"snap_share_mean"}
            assert not extra, f"{key}: season_totals carries {extra} absent from every period row"
