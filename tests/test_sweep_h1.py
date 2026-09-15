"""Brief 022 H1: determination, executable net, persistence - the pure pieces."""
import pytest

from research.sweep import common as S
from research.sweep import h1_settlement as H
from venues.mapping import team_abbr


def test_event_blob_splits_into_exactly_one_team_pair():
    assert H.split_event_teams("26SEP13BUFHOU", team_abbr) == ("BUF", "HOU")
    assert H.split_event_teams("26SEP10SFLAR", team_abbr) == ("SF", "LA")
    assert H.split_event_teams("26SEP13CLEJAC", team_abbr) == ("CLE", "JAX")
    with pytest.raises(ValueError):
        H.split_event_teams("26SEP13XXXYYY", team_abbr)


def test_prop_yes_is_determined_on_the_kth_counted_play():
    assert H.prop_determination([10, 20, 30], 2, game_end=100) == (1.0, 20, "yes_midgame")
    assert H.prop_determination([10, 20, 100], 3, game_end=100) == (1.0, 100, "at_end")
    assert H.prop_determination([10], 2, game_end=100) == (0.0, 100, "at_end")


def test_total_over_is_determined_when_combined_score_first_exceeds_the_line():
    path = [(10, 7), (20, 14), (30, 45), (40, 52)]
    assert H.total_determination(path, 44.5, 90) == (1.0, 30, "yes_midgame")
    assert H.total_determination(path, 60.5, 90) == (0.0, 90, "at_end")


def test_true_side_price():
    assert H.truth_ask(1.0, 0.97, 0.99) == 0.99
    assert H.truth_ask(0.0, 0.02, 0.05) == pytest.approx(0.98)
    assert H.truth_ask(0.0, None, 0.05) is None
    assert H.truth_ask(1.0, 0.99, None) is None


def test_net_at_an_extreme_price_pays_one_rounded_cent_on_the_order():
    # fee = ceil(0.07 * 10 * 0.99 * 0.01 = 0.00693) = $0.01 on the order = 0.1c/contract
    assert H.net_per_contract(0.99, 10, 1) == pytest.approx(0.01 - 0.001)
    assert H.net_per_contract(1.0, 10, 1) is None
    assert H.net_per_contract(None, 10, 1) is None


def test_asof_quote_refuses_stale_and_closed():
    qts, q = [100, 200], [(100, .9, .95), (200, .97, .99)]
    assert H.asof_quote(qts, q, 250, close_ts=1000)[0] == (200, .97, .99)
    assert H.asof_quote(qts, q, 200 + 661, close_ts=5000) == (None, "stale")
    assert H.asof_quote(qts, q, 900, close_ts=900) == (None, "closed")
    assert H.asof_quote(qts, q, 50, close_ts=900) == (None, "no quote")


def test_persistence_ends_on_price_close_or_staleness():
    after = [(110, .97, .98), (120, .98, .99), (130, .99, None)]
    assert H.persistence(after, 100, 1.0, 10, 1, close_ts=1000) == (20, "price moved")
    assert H.persistence(after, 100, 1.0, 10, 1, close_ts=115) == (10, "market closed")
    assert H.persistence([(900, .97, .98)], 100, 1.0, 10, 1, close_ts=5000) == (0, "quotes went stale")
    assert H.persistence(after[:2], 100, 1.0, 10, 1, close_ts=5000) == (20, "quotes ended")


def test_rung_check_ties_ticker_outcome_and_kalshi_strike():
    assert H.check_rung("KXNFLREC", "KXNFLREC-26SEP13BUFHOU-HOUXHUTCHINSON19-3", 2.5, "over", "receptions", 2.5) == 3
    with pytest.raises(AssertionError):
        H.check_rung("KXNFLREC", "KXNFLREC-26SEP13BUFHOU-HOUXHUTCHINSON19-3", 3.5, "over", "receptions", 3.5)
    with pytest.raises(AssertionError):
        H.check_rung("KXNFLRSHATT", "KXNFLRSHATT-26SEP13BUFHOU-X-3", 2.5, "over", "receptions", 2.5)


def test_settled_payload_parsing():
    rec = {"payload": {"markets": [{"ticker": "KXNFLREC-26SEP13BUFHOU-HOUXHUTCHINSON19-3", "status": "finalized",
                                    "result": "yes", "close_time": "2026-09-13T20:41:45Z",
                                    "settlement_ts": "2026-09-13T20:43:48.450791Z", "floor_strike": 2.5,
                                    "strike_type": "greater", "event_ticker": "KXNFLREC-26SEP13BUFHOU"}]}}
    st = H.parse_settled([rec])["KXNFLREC-26SEP13BUFHOU-HOUXHUTCHINSON19-3"]
    assert st["result"] == "yes" and st["settle_ts"] - st["close_ts"] == pytest.approx(123.450791)


def test_week2_settlement_rows_are_refused(tmp_path):
    import gzip, json
    d = tmp_path / "2026-09-22"
    d.mkdir()
    with gzip.open(d / "00.jsonl.gz", "wt", encoding="utf-8") as f:
        f.write(json.dumps({"payload": {"markets": [{"ticker": "KXNFLREC-26SEP20NOBAL-X-3"}]}}) + "\n")
    with pytest.raises(S.HoldoutViolation):
        H.load_settlements(str(tmp_path))
