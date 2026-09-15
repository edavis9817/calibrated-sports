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


def test_week1_default_population_path_and_role_are_unchanged(tmp_path, monkeypatch):
    import os
    assert S.POPULATION == "nfl_wk1" and S.SEASON == 2026 and S.WEEK == 1 and S.ROLE == "search"
    assert H.REGISTRY == os.path.join(S.ROOT, "research", "sweep", "results", "h1.jsonl")
    reg = S.Registry(str(tmp_path / "h1.jsonl"))
    res = {"est": 0.02, "lo": 0.01, "hi": 0.03, "se": 0.005, "p": 1e-4, "n": 20, "games": 8}
    H.register_cell(reg, "H1_net_mean", "KXNFLREC|yes_midgame|G120|C10", res, res,
                    role=S.ROLE, population=S.POPULATION)
    recs = S.load_registries([str(tmp_path / "h1.jsonl")])
    assert [(r["family"], r["role"], r["population"]) for r in recs] == [
        ("H1_net_mean", "search", "nfl_wk1"), ("H1_net_share", "descriptive", "nfl_wk1")]
    assert recs[0]["est"] == pytest.approx(2.0)          # stored in pp


def test_cfb_registers_replication_and_population_search_twice(tmp_path):
    reg = S.Registry(str(tmp_path / "h1_cfb.jsonl"))
    res = {"est": 0.01, "lo": 0.0, "hi": 0.02, "se": 0.005, "p": 0.05, "n": 9, "games": 6}
    H.register_cell(reg, "H1_net_mean", "KXNCAAFSPREAD|at_end|G120|C10", res, res,
                    role="replication", population="cfb")
    H.register_cell(reg, "H1_net_mean cfb population", "KXNCAAFSPREAD|at_end|G120|C10", res, res,
                    role="search", population="cfb", share_family="H1_net_share cfb population")
    recs = S.load_registries([str(tmp_path / "h1_cfb.jsonl")])
    assert [(r["family"], r["role"]) for r in recs] == [
        ("H1_net_mean", "replication"), ("H1_net_share", "descriptive"),
        ("H1_net_mean cfb population", "search"), ("H1_net_share cfb population", "descriptive")]
    assert {r["population"] for r in recs} == {"cfb"}


def _cfb_game():
    from research import cfb_calibration as CC
    return {"nh": CC.norm("Houston"), "na": CC.norm("Arkansas St."), "hp": 24, "ap": 31}


@pytest.mark.parametrize("series,subject,line,truth", [
    ("KXNCAAFGAME", "Arkansas St.", None, 1.0),
    ("KXNCAAFGAME", "Houston", None, 0.0),
    ("KXNCAAFSPREAD", "Arkansas St. wins by over 6.5 points", 6.5, 1.0),
    ("KXNCAAFSPREAD", "Arkansas St. wins by over 7.5 points", 7.5, 0.0),
    ("KXNCAAFSPREAD", "Houston wins by over 1.5 points", 1.5, 0.0),
    ("KXNCAAFTOTAL", "Over 54.5 points scored", 54.5, 1.0),
    ("KXNCAAFTOTAL", "Over 55.5 points scored", 55.5, 0.0),
    ("KXNCAAFTEAMTOTAL", "Houston over 23.5 points scored", 23.5, 1.0),
    ("KXNCAAFTEAMTOTAL", "Arkansas St. over 31.5 points scored", 31.5, 0.0),
])
def test_cfb_truth_orientation(series, subject, line, truth):
    assert H.cfb_truth(series, subject, line, _cfb_game()) == truth


def test_cfb_truth_refuses_a_subject_naming_neither_team():
    with pytest.raises(ValueError):
        H.cfb_truth("KXNCAAFSPREAD", "Alabama wins by over 3.5 points", 3.5, _cfb_game())


def test_cfb_determination_is_post_kickoff_and_flags_comebacks():
    pre_game_fav = [(50, .97, .98), (200, .60, .62), (400, .97, .99)]
    assert H.cfb_determination(pre_game_fav, [], kickoff=100) == (400, False)
    loser = [(150, .97, .98)]
    assert H.cfb_determination(pre_game_fav, loser, kickoff=100) == (400, True)
    assert H.cfb_determination([(150, None, .99)], [], kickoff=100) == (None, False)


def test_book_touch_from_raw_orderbook():
    snap = (10.0, 0.40, 25.0, 0.55, 7.0)        # yes bid .40 x25, no bid .55 x7
    assert H.book_touch(snap, "buy_yes") == (10.0, pytest.approx(0.45), 7.0)
    assert H.book_touch(snap, "buy_no") == (10.0, pytest.approx(0.60), 25.0)
    assert H.book_touch((10.0, None, None, 0.55, 7.0), "buy_no") is None
    assert H.book_top([["0.0100", "5.00"], ["0.0600", "100.00"]]) == (0.06, 100.0)
    assert H.book_top([]) == (None, None)


def test_cfb_depth_reader_respects_the_60s_window():
    books = {"T": [(100.0, 0.40, 25.0, 0.55, 7.0)]}
    depth, window = H.cfb_depth_readers(books)
    assert depth("T", "buy_yes", 150.0)[2] == 7.0
    assert depth("T", "buy_yes", 161.0) is None and depth("T", "buy_yes", 99.0) is None
    assert depth("missing", "buy_yes", 150.0) is None
    assert window("T", "buy_no", 90.0, 110.0) == [(100.0, pytest.approx(0.60), 25.0)]


def test_unreadable_region():
    cover = {3600: (5000.0, False), 7200: (7300.0, True)}
    assert not H.unreadable_at(4000.0, cover)          # before the corrupt byte
    assert H.unreadable_at(5500.0, cover)              # after it
    assert not H.unreadable_at(10000.0, cover)         # complete shard
    assert H.unreadable_at(20000.0, cover)             # no shard for that hour


def test_week2_settlement_rows_are_refused(tmp_path):
    import gzip, json
    d = tmp_path / "2026-09-22"
    d.mkdir()
    with gzip.open(d / "00.jsonl.gz", "wt", encoding="utf-8") as f:
        f.write(json.dumps({"payload": {"markets": [{"ticker": "KXNFLREC-26SEP20NOBAL-X-3"}]}}) + "\n")
    with pytest.raises(S.HoldoutViolation):
        H.load_settlements(str(tmp_path))
