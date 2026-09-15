"""Brief 023 Part 1: walk-forward is enforced in code, constants are restored,
and features are strictly as-of."""
import math

import pytest

import config
import store
from research import walkforward as W


def test_a_constant_fit_on_the_target_season_refuses():
    W.Constants(2024, {}, {"MEAN_DRIFT_VAR": (2022, 2023)}).check()
    with pytest.raises(W.LeakError):
        W.Constants(2024, {}, {"MEAN_DRIFT_VAR": (2023, 2024)}).check()
    with pytest.raises(W.LeakError):
        W.Constants(2024, {}, {"X": (2025,)}).check()


def test_constants_for_uses_the_two_seasons_before_the_target():
    seen = []

    def fake_drift(a, b):
        seen.append((a, b))
        return {"receptions": 1.0, "rush_attempts": 8.0}, {}

    c = W.constants_for(2025, {"TEAM_CHANGE_KEEP": 1.0, "_drift_scale": 2.0}, fake_drift)
    assert seen == [(2023, 2024)]
    assert c.fit_seasons["MEAN_DRIFT_VAR"] == (2023, 2024)
    assert c.values["TEAM_CHANGE_KEEP"] == 1.0 and c.values["COACH_CHANGE_KEEP"] == 0.70
    assert c.values["MEAN_DRIFT_VAR"] == {"receptions": 2.0, "rush_attempts": 16.0}


def test_patched_restores_even_on_exception():
    from models import baseline
    before = (baseline.TEAM_CHANGE_KEEP, dict(baseline.MEAN_DRIFT_VAR))
    with pytest.raises(RuntimeError):
        with W.patched({"TEAM_CHANGE_KEEP": 0.1, "MEAN_DRIFT_VAR": {"receptions": 99.0}}):
            assert baseline.TEAM_CHANGE_KEEP == 0.1
            assert baseline.MEAN_DRIFT_VAR == {"receptions": 99.0}
            raise RuntimeError
    assert (baseline.TEAM_CHANGE_KEEP, dict(baseline.MEAN_DRIFT_VAR)) == before
    with pytest.raises(KeyError):
        with W.patched({"NOT_A_CONSTANT": 1}):
            pass


def test_drift_is_noise_corrected_method_of_moments():
    prev = {"a": [2.0, 4.0] * 3, "b": [5.0] * 6, "tiny": [9.0] * 3}
    cur = {"a": [4.0, 6.0] * 3, "b": [5.0] * 6, "tiny": [9.0] * 6}
    D, n = W.drift_from_panels(prev, cur, min_games=6, screen=0.0)
    # a: (5-3)^2 - 1/6 - 1/6 = 3.6667; b: 0; tiny excluded (3 games)
    assert n == 2 and D == pytest.approx((4 - 2 / 6) / 2)
    D2, n2 = W.drift_from_panels(prev, cur, min_games=6, screen=4.0)
    assert n2 == 1 and D2 == 0.0


def test_features_never_see_a_game_at_or_after_as_of(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "RAW_DIR", str(tmp_path / "raw"))
    store.init_db()
    import sqlite3
    from models import features
    con = sqlite3.connect(config.DB_PATH)
    t1, t2 = 1_700_000_000.0, 1_700_600_000.0
    for wk, t in ((1, t1), (2, t2)):
        con.execute("INSERT INTO nfl_games (sport, game_id, data_version, season, week, game_type, "
                    "kickoff_ts, home_team, away_team, source, ingested_ts) VALUES "
                    "('nfl', ?, 'v1', 2023, ?, 'REG', ?, 'BUF', 'MIA', 't', 0)", (f"2023_0{wk}_MIA_BUF", wk, t))
        con.execute("INSERT INTO nfl_player_week (sport, gsis_id, season, week, season_type, data_version, "
                    "team, receptions, source, ingested_ts) VALUES ('nfl', 'P1', 2023, ?, 'REG', 'v1', 'BUF', ?, 't', 0)",
                    (wk, 2.0 if wk == 1 else 10.0))
    con.commit()
    between = features.player_prior(con, "P1", "receptions", t2 - 1, seasons=(2023,))
    at = features.player_prior(con, "P1", "receptions", t2, seasons=(2023,))
    after = features.player_prior(con, "P1", "receptions", t2 + 1, seasons=(2023,))
    assert (between.n_games, between.mean) == (1, 2.0)
    assert (at.n_games, at.mean) == (1, 2.0)          # strictly before kickoff
    assert (after.n_games, after.mean) == (2, 6.0)


def test_scoring_helpers():
    assert W.brier(0.7, 1.0) == pytest.approx(0.09)
    assert W.logloss(1.0, 0.0) == pytest.approx(-math.log(W.CLIP))
    assert W.mde({"se": 0.01}) == pytest.approx(0.028) and W.mde(None) is None


def test_corrected_settlement_settles_a_player_who_played_at_zero_and_voids_a_dnp():
    assert W.corrected_result(0.5, False, 24) == ("under", 0.0)
    assert W.corrected_result(1.5, False, 3) == ("under", 0.0)
    assert W.corrected_result(0.5, False, 0) == ("void", None)
    assert W.corrected_result(0.5, False, None) == ("void", None)


def test_every_variant_is_declared_and_counted():
    assert len(W.VARIANTS) == 13 and W.VARIANTS[0] == ("default", {})
    names = [n for n, _ in W.VARIANTS]
    assert len(set(names)) == 13
