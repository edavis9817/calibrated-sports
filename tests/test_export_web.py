"""Brief W02 Part A: the web export honours the contract in docs/web-schema.md."""
import json
import os
import re

import pytest

import config
import store
from jobs import export_web as E

NOW = 1_789_500_000.0  # 2026-09-15


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "RAW_DIR", str(tmp_path / "raw"))
    store.init_db()
    import sqlite3
    c = sqlite3.connect(config.DB_PATH)
    games = [
        # game_id, season, week, type, gameday, kick, home, away, hs, as, spread, total, hc, ac
        ("2025_01_BUF_MIA", 2025, 1, "REG", "2025-09-07", NOW - 3e7, "MIA", "BUF", 20, 27, -2.5, 47.5, "McD", "Mc"),
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
        ("00-A", 2026, 1, "REG", "Wide One", "WR", "BUF", "HOU", 5, 8, 60, 0, 1, 5, 0, 0, 11.5),
        ("00-D", 2026, 1, "REG", "Line Backer", "LB", "BUF", "HOU", 0, 0, 0, 0, 0, 0, 0, 0, 0.0),
    ]
    for w in weeks:
        c.execute("INSERT INTO nfl_player_week (gsis_id, season, week, season_type, data_version, "
                  "player_name, position, team, opponent, receptions, targets, receiving_yards, "
                  "receiving_tds, carries, rushing_yards, rushing_tds, attempts, fantasy_points_ppr, "
                  "def_tackles_solo, source, ingested_ts) VALUES (?,?,?,?,'v1',?,?,?,?,?,?,?,?,?,?,?,?,?,3,'t',0)", w)
    c.execute("INSERT INTO player_xwalk (gsis_id, display_name, position, pfr_id) VALUES ('00-A','Wide One','WR','WideWi00')")
    c.execute("INSERT INTO nfl_snap_counts (pfr_player_id, game_id, data_version, season, week, player, "
              "offense_snaps, offense_pct, source, ingested_ts) VALUES ('WideWi00','2026_01_BUF_HOU','v1',2026,1,'Wide One',54,0.87,'t',0)")
    c.commit()
    c.close()
    return tmp_path


def test_config_has_no_default_destination():
    src = open(os.path.join(E.ROOT, "config.py"), encoding="utf-8").read()
    assert re.search(r'WEB_DATA_DIR = os\.getenv\("WEB_DATA_DIR"\)\s*$', src, re.M)
    assert re.search(r'WEB_REPO_DIR = os\.getenv\("WEB_REPO_DIR"\)\s*$', src, re.M)


def test_export_refuses_without_a_destination(monkeypatch):
    monkeypatch.setattr(config, "WEB_DATA_DIR", None)
    with pytest.raises(E.ConfigError):
        E.export(only=["manifest"])


def test_team_spread_is_from_the_teams_own_perspective():
    assert E.team_spread(3.0, home=True) == 3.0
    assert E.team_spread(3.0, home=False) == -3.0
    assert E.team_spread(None, home=True) is None


def test_fantasy_scoring_arithmetic():
    row = {"receptions": 7, "receiving_yards": 88, "receiving_tds": 1, "rushing_yards": 12,
           "rushing_tds": 0, "passing_yards": 0, "passing_tds": 0, "interceptions": 0}
    assert E.fantasy_points(row, "ppr") == pytest.approx(7 + 8.8 + 6 + 1.2)
    assert E.fantasy_points(row, "half") == pytest.approx(3.5 + 8.8 + 6 + 1.2)
    assert E.fantasy_points(row, "standard") == pytest.approx(8.8 + 6 + 1.2)
    qb = {"passing_yards": 250, "passing_tds": 2, "interceptions": 1}
    assert E.fantasy_points(qb, "ppr") == pytest.approx(10 + 8 - 2)


def test_distribution_summary_shape():
    sims = list(range(0, 40))
    d = E.distribution_summary(sims)
    assert len(d["cdf"]) == 51 and d["cdf"][0] == {"x": 0, "p_at_most": 0.025}
    assert [t["points"] for t in d["thresholds"]] == [5, 10, 15, 20, 25, 30]
    assert d["thresholds"][0]["p_at_least"] == pytest.approx(35 / 40)
    assert set(d["quantiles"]) == {"q10", "q25", "q50", "q75", "q90"}


def test_stale_detection():
    games = {"a": {"season": 2026, "week": 1, "home_score": 20},
             "b": {"season": 2026, "week": 2, "home_score": None}}
    fresh = E.current_week(games, [{"season": 2026, "week": 1}], NOW)
    assert fresh["week"] == 2 and not fresh["stale"]
    games["b"]["home_score"] = 10
    games["c"] = {"season": 2026, "week": 3, "home_score": None}
    late = E.current_week(games, [{"season": 2026, "week": 1}], NOW)
    assert late["stale"] and "week 2" in late["stale_reason"]


def test_every_file_carries_the_envelope_and_rewrites_only_on_change(db):
    dest = str(db / "out")
    s1 = E.export(only=["players", "teams", "manifest"], now_ts=NOW, dest=dest)
    files = [os.path.join(r, f) for r, _, fs in os.walk(dest) for f in fs]
    assert files
    for p in files:
        obj = json.load(open(p, encoding="utf-8"))
        assert obj["schema_version"] == 1 and obj["generated_at"] and obj["kind"]
    man = json.load(open(os.path.join(dest, "manifest.json"), encoding="utf-8"))
    assert [p["id"] for p in man["players"]] == ["00-A"]            # LB has no offensive usage
    assert man["current"]["season"] == 2026 and man["current"]["week"] == 2
    player = json.load(open(os.path.join(dest, "players", "00-A.json"), encoding="utf-8"))
    g26 = [g for g in player["games"] if g["season"] == 2026][0]
    assert g26["snap_share"] == 0.87 and g26["home"] is False and g26["opponent"] == "HOU"
    assert g26["fumbles_lost"] is None and "fumbles_lost" in player["fantasy_scoring"]["note"]
    buf = json.load(open(os.path.join(dest, "teams", "BUF.json"), encoding="utf-8"))
    at_hou = [s for s in buf["schedule"] if s["game_id"] == "2026_01_BUF_HOU"][0]
    assert at_hou["spread"] == -1.5 and at_hou["result"] == "W"
    lv = json.load(open(os.path.join(dest, "teams", "LV.json"), encoding="utf-8"))
    assert any(s["game_id"] == "2012_01_OAK_SD" for s in lv["schedule"])   # OAK -> LV
    before = {p: os.path.getmtime(p) for p in files}
    s2 = E.export(only=["players", "teams", "manifest"], now_ts=NOW + 60, dest=dest)
    assert s2["players"] == (0, 0) and s2["teams"] == (0, 0) and s2["manifest"] is False
    assert all(os.path.getmtime(p) == t for p, t in before.items())


def test_stale_files_are_deleted_but_legacy_root_files_never(db):
    dest = str(db / "out")
    os.makedirs(os.path.join(dest, "players"))
    open(os.path.join(dest, "players", "00-GONE.json"), "w").write("{}")
    open(os.path.join(dest, "site.json"), "w").write("{}")
    E.export(only=["players"], now_ts=NOW, dest=dest)
    assert not os.path.exists(os.path.join(dest, "players", "00-GONE.json"))
    assert os.path.exists(os.path.join(dest, "site.json"))


def test_hypotheses_source_uses_only_the_contracts_verdicts():
    src = json.load(open(os.path.join(E.ROOT, "docs", "hypotheses.json"), encoding="utf-8"))
    assert src["hypotheses"]
    for h in src["hypotheses"]:
        assert h["verdict"] in {"retired", "null", "not_testable", "open"}
        assert h["interval"] is None or (len(h["interval"]) == 2 and h["interval"][0] <= h["interval"][1])
        assert os.path.exists(os.path.join(E.ROOT, h["script"])), h["script"]
