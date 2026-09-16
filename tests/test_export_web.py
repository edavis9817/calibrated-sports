"""Contract v2: the web export honours docs/web-schema.md."""
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
        ("00-D", 2026, 1, "REG", "Line Backer", "LB", "BUF", "HOU", 0, 0, 0, 0, 0, 0, 0, 0, 0.0),
        ("00-S1", 2025, 1, "REG", "Josh Allen", "QB", "BUF", "MIA", 0, 0, 0, 0, 5, 30, 1, 30, 20.0),
        ("00-S2", 2026, 1, "REG", "Josh Allen", "LB", "BUF", "HOU", 1, 1, 5, 0, 0, 0, 0, 0, 1.5),
    ]
    for w in weeks:
        c.execute("INSERT INTO nfl_player_week (gsis_id, season, week, season_type, data_version, "
                  "player_name, position, team, opponent, receptions, targets, receiving_yards, "
                  "receiving_tds, carries, rushing_yards, rushing_tds, attempts, fantasy_points_ppr, "
                  "def_tackles_solo, source, ingested_ts) VALUES (?,?,?,?,'v1',?,?,?,?,?,?,?,?,?,?,?,?,?,3,'t',0)", w)
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
    assert p26["stats"]["fum_lost"] is None
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
    assert {"slug": "buf", "abbr": "BUF", "name": "Buffalo Bills"} in m["teams"]


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
    def __init__(self):
        self.puts, self.deletes = {}, []

    def put_object(self, Bucket, Key, Body, ContentType, CacheControl):
        self.puts[Key] = {"bucket": Bucket, "body": Body, "type": ContentType, "cache": CacheControl}

    def delete_object(self, Bucket, Key):
        self.deletes.append(Key)


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
    assert r2["changed"] == 0 and s3b.puts == {}

    E.write_if_changed(E.local_path(dest, "nfl/manifest.json"), {"kind": "x", "changed": True})
    os.remove(E.local_path(dest, "nfl/players/00-A/summary.json"))
    s3c = FakeS3()
    r3 = E.upload(dest=dest, client=s3c, log=lambda *_: None)
    assert list(s3c.puts) == ["nfl/manifest.json"]
    assert s3c.deletes == ["nfl/players/00-A/summary.json"] and r3["deleted"] == 1
    state = json.load(open(os.path.join(dest, E.STATE_FILE), encoding="utf-8"))
    assert "nfl/players/00-A/summary.json" not in state


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


def test_hypotheses_source_uses_only_the_contracts_verdicts():
    src = json.load(open(os.path.join(E.ROOT, "docs", "hypotheses.json"), encoding="utf-8"))
    assert src["hypotheses"]
    for h in src["hypotheses"]:
        assert h["verdict"] in {"retired", "null", "not_testable", "open"}
        assert h["interval"] is None or (len(h["interval"]) == 2 and h["interval"][0] <= h["interval"][1])
        assert os.path.exists(os.path.join(E.ROOT, h["script"])), h["script"]
