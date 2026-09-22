"""Baseline model, pricing and ledger tests. Run: pytest -q

Two of these are the acceptance criteria and both guard failures that are
invisible rather than loud: a feature dated after as_of_ts produces a model that
backtests beautifully and is worthless live, and a position that is +EV before
fees and -EV after is one you will happily take all season.
"""
import sqlite3
import time
from decimal import Decimal

import numpy as np
import pytest

import config
import evaluation
import store
from core.distributions import (NegativeBinomial, ZeroInflatedGamma,
                                edge_after_fees, kalshi_fee)
from jobs import paper_trade
from models import baseline, features


# --- invariant #3: the distribution round trip -------------------------------

@pytest.mark.parametrize("mean,vmr", [(3.4, 1.8), (12.1, 3.7), (0.6, 1.3)])
def test_negbin_sample_matches_its_own_mean(mean, vmr):
    d = NegativeBinomial(mean, vmr)
    s = d.sample(100_000, np.random.default_rng(0))
    assert s.mean() == pytest.approx(d.mean(), rel=0.02)


@pytest.mark.parametrize("mean,vmr", [(3.4, 1.8), (12.1, 3.7)])
def test_negbin_cdf_and_prob_over_agree_with_sampling(mean, vmr):
    """cdf, prob_over and the sampler are three routes to the same number. If
    they disagree, one of them is what the model is actually pricing on and it
    is not obvious which."""
    d = NegativeBinomial(mean, vmr)
    s = d.sample(200_000, np.random.default_rng(1))
    for line in (0.5, 2.5, 5.5, 9.5):
        assert d.prob_over(line) == pytest.approx(1.0 - d.cdf(line), abs=1e-9)
        assert d.prob_over(line) == pytest.approx((s > line).mean(), abs=0.01)


def test_zig_round_trips_mean_and_zero_mass():
    d = ZeroInflatedGamma.from_overall_moments(47.7, 36.9 ** 2, 0.058)
    s = d.sample(100_000, np.random.default_rng(2))
    assert s.mean() == pytest.approx(d.mean(), rel=0.03)
    assert (s == 0).mean() == pytest.approx(0.058, abs=0.005)
    assert d.prob_over(49.5) == pytest.approx((s > 49.5).mean(), abs=0.01)


def test_a_stored_prediction_rebuilds_into_the_same_distribution():
    """Predictions store parameters, not a probability, precisely so that a
    later question the cache never anticipated can still be answered."""
    d = NegativeBinomial(4.2, 1.9)
    rebuilt = baseline.rebuild("negative_binomial",
                               {"mean": 4.2, "var_mean_ratio": 1.9})
    for line in (1.5, 3.5, 7.5):
        assert rebuilt.prob_over(line) == pytest.approx(d.prob_over(line))


# --- the fee is not a rounding detail ----------------------------------------

def test_a_position_can_be_positive_gross_and_negative_net():
    """THE acceptance case. A 1.5-cent edge on a near-coin-flip is real before
    fees and gone after - and the fee peaks exactly there, at P=0.5."""
    fair, price = 0.515, 0.50
    gross = fair - price
    net = edge_after_fees(fair, price, 100)

    assert gross > 0, "should be +EV before fees"
    assert net < 0, "must be -EV after fees"
    # Brief 016: the fee is a Decimal now, so compare against one. Mixing it
    # with pytest.approx's float arithmetic raises rather than fails.
    assert kalshi_fee(0.50, 1) == Decimal("0.02")


def test_the_same_edge_survives_at_the_tails():
    """The fee is 0.07*P*(1-P), so it collapses away from 0.5. An edge that
    dies on a coin flip lives comfortably at a longshot price - which changes
    which side of a market is worth taking, not just how much it is worth."""
    edge = 0.015
    assert edge_after_fees(0.50 + edge, 0.50, 100) < 0
    assert edge_after_fees(0.06 + edge, 0.06, 100) > 0
    # The raw fee ratio is ~4x, but at ONE contract the ceiling-to-whole-cents
    # flattens it to 2c vs 1c. Size is what exposes the real shape, which is
    # also the only regime where it matters.
    assert kalshi_fee(0.06, 1000) < kalshi_fee(0.50, 1000) / 3


def test_evaluate_prices_the_side_it_actually_takes():
    """The fee depends on the price PAID, so it differs between yes and no and
    has to be computed on the side taken, not on the yes price by habit."""
    ev = paper_trade.evaluate(0.20, bid=0.60, ask=0.64)
    assert ev["side"] == "no"
    assert ev["model_prob"] == pytest.approx(0.80)
    assert ev["market_prob"] == pytest.approx(0.38)
    assert ev["spread"] == pytest.approx(0.04)
    # Brief 016: evaluate() now prices the real ticket, PER CONTRACT.
    from core.fees import fee_per_contract
    assert ev["fee"] == pytest.approx(fee_per_contract(0.38, 100))
    assert ev["net_edge"] == pytest.approx(ev["gross_edge"] - ev["fee"])


def test_the_mid_is_recorded_with_its_spread():
    """A mid inside a wide spread is not a tradeable price. The backtest has to
    be able to tell the two apart later, so the spread rides on every ticket."""
    tight = paper_trade.evaluate(0.70, bid=0.59, ask=0.61)
    wide = paper_trade.evaluate(0.70, bid=0.50, ask=0.70)
    assert tight["market_prob"] == wide["market_prob"] == pytest.approx(0.60)
    assert tight["spread"] == pytest.approx(0.02)
    assert wide["spread"] == pytest.approx(0.20)
    assert wide["spread"] > paper_trade.MAX_SPREAD    # filtered before a ticket


# --- invariant #5: nothing dated after as_of_ts ------------------------------

def test_provenance_rejects_a_feature_from_the_future():
    p = features.Provenance()
    p.add("nfl_player_week:x", 2000.0, 10)
    p.add("positional:WR", 3000.0, 40)
    assert p.assert_as_of(3001.0) is True
    with pytest.raises(features.LeakError) as e:
        p.assert_as_of(2500.0)
    assert "positional:WR" in str(e.value)


def test_provenance_boundary_is_strict():
    """A feature timestamped exactly at as_of_ts is a leak: the game kicked off
    at that instant, so its result was not knowable a moment before."""
    p = features.Provenance()
    p.add("s", 1000.0, 1)
    with pytest.raises(features.LeakError):
        p.assert_as_of(1000.0)
    assert p.assert_as_of(1000.1) is True


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "RAW_DIR", str(tmp_path / "raw"))
    store.init_db()
    now = time.time()
    # A full 2025 season, all of it genuinely in the past. Spacing forward from
    # a recent date put later weeks in the future, and the guard correctly made
    # them invisible - a fixture bug that read as a code bug twice.
    past, future = now - 86400 * 400, now + 86400
    # One game row per player-week: the as-of join reads the kickoff from the
    # game, so a player-week with no game is invisible - which is the guard
    # working, and was a bug in this fixture before it was a test.
    games = [("nfl", f"2025_{w:02d}_DET_GB", "v1", 2025, w, "2025-09-07",
              "GB", "DET", past + w * 86400 * 7, "test", now)
             for w in range(1, 11)]
    games.append(("nfl", "2026_01_DET_CHI", "v1", 2026, 1, "2026-09-13",
                  "CHI", "DET", future, "test", now))
    store.replace_rows(
        "nfl_games",
        ("sport", "game_id", "data_version", "season", "week", "gameday",
         "home_team", "away_team", "kickoff_ts", "source", "ingested_ts"),
        games)
    rows = [("nfl", "00-0000001", 2025, w, "REG", "v1", "Test WR", "WR", "DET",
             float(4 + (w % 3)), "test", now) for w in range(1, 11)]
    store.replace_rows(
        "nfl_player_week",
        ("sport", "gsis_id", "season", "week", "season_type", "data_version",
         "player_name", "position", "team", "receptions", "source",
         "ingested_ts"), rows)
    yield tmp_path


def test_features_cannot_see_a_game_that_has_not_happened(env):
    """The guard is in the join, so a leaking query returns nothing rather than
    returning something plausible."""
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    now = time.time()
    prior = features.player_prior(con, "00-0000001", "receptions", now, (2025,))
    assert prior.n_games == 10
    prior.provenance.assert_as_of(now)

    # As of a year ago, none of those games had been played.
    stale = features.player_prior(con, "00-0000001", "receptions",
                                  now - 86400 * 400, (2025,))
    assert stale.n_games == 0
    con.close()


def test_a_fit_reports_provenance_that_predates_as_of(env):
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    now = time.time()
    fit = baseline.fit_player_stat(con, "00-0000001", "receptions", 2026, now,
                                   "WR", "DET")
    assert fit.provenance.assert_as_of(now) is True
    assert fit.provenance.newest < now
    assert fit.prior_games == 10
    assert 0.0 < fit.shrink_weight <= 1.0
    assert fit.dist.mean() > 0
    con.close()


def test_shrinkage_pulls_toward_the_target_and_weakens_with_change(env):
    """A team change and a coach change both cut how much of a player's own
    history survives - Week 1 priors are a year stale and the roster moved."""
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    now = time.time()
    same = baseline.fit_player_stat(con, "00-0000001", "receptions", 2026, now,
                                    "WR", "DET")
    moved = baseline.fit_player_stat(con, "00-0000001", "receptions", 2026, now,
                                     "WR", "CHI")
    assert moved.shrink_weight < same.shrink_weight
    assert "team DET->CHI" in moved.notes
    con.close()


# --- evaluation stubs --------------------------------------------------------

def test_brier_prefers_the_better_forecast():
    good = [(0.9, 1, "m", "receptions"), (0.1, 0, "m", "receptions")]
    bad = [(0.1, 1, "m", "receptions"), (0.9, 0, "m", "receptions")]
    assert evaluation.brier(good) < evaluation.brier(bad)
    assert evaluation.brier([(0.5, 1, "m", "s")]) == pytest.approx(0.25)
    assert evaluation.brier([]) is None


def test_calibration_buckets_are_fixed_in_advance():
    rows = [(0.05, 0, "m", "s"), (0.95, 1, "m", "s"), (0.92, 1, "m", "s")]
    cal = evaluation.calibration(rows)
    assert len(cal) == 10
    assert cal[0]["n"] == 1 and cal[0]["realized"] == 0.0
    assert cal[9]["n"] == 2 and cal[9]["realized"] == 1.0
    assert cal[5]["n"] == 0 and cal[5]["realized"] is None   # empty, not zero


def test_pushes_are_dropped_not_scored_as_half(env):
    """Scoring a push as 0.5 would reward the model for landing on a line it
    never took a position against."""
    now = time.time()
    store.record_prediction({
        # The version must embed the fingerprint or the store refuses it -
        # see store.VersionMismatch and test_version_derivation.py.
        "outcome_id": "abc", "model_version": "test-0.0+f", "code_fingerprint": "f",
        "as_of_ts": now, "created_ts": now, "family": "negative_binomial",
        "params_json": "{}", "mean": 4.0, "prob_over": 0.6, "push_prob": 0.1,
        "prior_games": 10, "shrink_weight": 0.5})
    store.record_settlement("abc", "push", 4.0, "v1", "nflverse")
    store.replace_rows(
        "outcomes",
        ("outcome_id", "key", "sport", "season", "week", "entity_type",
         "entity_id", "side", "push_possible", "created_ts"),
        [("abc", "k", "nfl", 2026, 1, "player", "p", "over", 1, now)])

    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    assert evaluation.scored_rows(con) == []
    con.close()
