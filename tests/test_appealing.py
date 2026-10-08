"""a-70: board/nfl/appealing.json - cost, the ranking, the band flag, the ordering
sentence, the contract, and that a failed build leaves the previous file alone.

Every verdict here is driven to BOTH of its answers: a flag that cannot be false,
or an ordering sentence that cannot read the other way, would be decoration.
"""
import json
import os
import sqlite3

import pytest

from core import fees as F
from jobs import appealing_export as A

NOW = 1_791_440_000.0
KICK = NOW + 86_400


def claim(text="DAL wins"):
    return {"text": text, "subject_type": "team", "subject": "DAL", "name": "DAL", "stat": None,
            "line": None, "direction": "wins"}


def xrow(rid, p_model, bid, ask, market="moneyline", mid=None):
    m = "KXNFLGAME-26OCT08TBDAL-DAL" if mid is None else mid
    return A.make_row(
        row_id=rid, market=market, venue="kalshi", game_id="2026_05_TB_DAL", kickoff=A.iso(KICK),
        claim=claim(), p_market=(bid + ask) / 2, p_model=p_model,
        market_price={"basis": "exchange_mid", "bid": bid, "ask": ask, "books": None,
                      "read_at": A.iso(NOW), "instrument": m},
        model={"model": "game_moneyline", "as_of": A.iso(NOW)},
        cost_for=lambda side: A.exchange_cost(bid, ask, side, m))


def brow(rid, p_model, p_mkt, over, under, market="prop"):
    q = [{"over": over, "under": under}]
    return A.make_row(
        row_id=rid, market=market, venue="books", game_id="2026_05_TB_DAL", kickoff=A.iso(KICK),
        claim=claim("X: more than 3.5 receptions"), p_market=p_mkt, p_model=p_model,
        market_price={"basis": "median_devig_mult", "bid": None, "ask": None, "books": 1,
                      "read_at": A.iso(NOW), "instrument": None},
        model={"model": "prop_baseline", "as_of": A.iso(NOW)},
        cost_for=lambda side: A.book_cost(q, side)[0])


# ----------------------------------------------------------------- cost

def test_exchange_cost_is_half_the_spread_plus_the_series_taker_fee_on_the_ticket():
    c = A.exchange_cost(0.46, 0.48, "claim", "KXNFLSPREAD-26OCT08TBDAL-DAL10")
    fee = float(F.kalshi_fee(0.48, A.TICKET, "taker", 1)) / A.TICKET
    assert c["half_spread_pp"] == 1.0 and c["fee_pp"] == round(fee * 100, 2)
    assert c["total_pp"] == round((0.01 + fee) * 100, 2) and c["price"] == 0.48
    # the other side pays the NO price, 1 - bid
    assert A.exchange_cost(0.46, 0.48, "against", "KXNFLSPREAD-X")["price"] == 0.54
    # the fee is the series' own: it is quadratic in price, so a tail rung is cheaper
    tail = A.exchange_cost(0.95, 0.97, "claim", "KXNFLTOTAL-X")
    assert tail["fee_pp"] < c["fee_pp"] and tail["half_spread_pp"] == c["half_spread_pp"]


def test_book_cost_scales_with_price_and_is_never_flat():
    even, _ = A.book_cost([{"over": -110, "under": -110}], "claim")
    deep, _ = A.book_cost([{"over": -400, "under": 300}], "claim")
    dog, _ = A.book_cost([{"over": -400, "under": 300}], "against")
    assert even["total_pp"] == pytest.approx(2.38, abs=0.01)
    assert deep["total_pp"] > even["total_pp"] > dog["total_pp"]      # c-22: cost follows price
    assert deep["basis"] == "book_devig" and deep["fee_pp"] is None
    assert A.book_cost([{"over": -110, "under": None}], "claim") == (None, None)


# ----------------------------------------------------------------- the row and the ranking

def test_side_is_the_sign_of_the_difference_and_net_is_gap_minus_cost():
    up, down = xrow("a", 0.60, 0.49, 0.51), xrow("b", 0.40, 0.49, 0.51)
    assert (up["side"], down["side"]) == ("claim", "against")
    assert up["difference_pp"] == 10.0 and down["difference_pp"] == -10.0
    for r in (up, down):
        assert r["net_pp"] == round(abs(r["difference_pp"]) - r["cost"]["total_pp"], 2)
        assert r["ordering_note"] == A.ORDERING_NOTE and r["flagged"] is False


def test_rank_is_net_of_cost_not_raw_difference_and_ties_go_to_the_cheaper():
    wide = xrow("wide", 0.62, 0.40, 0.60)            # 12 pt gap, 10 pt half-spread
    tight = xrow("tight", 0.56, 0.495, 0.505)        # 6 pt gap, 0.5 pt half-spread
    assert abs(wide["difference_pp"]) > abs(tight["difference_pp"])
    assert [r["row_id"] for r in A.rank([wide, tight])] == ["tight", "wide"]
    # equal net: the cheaper cost first, then the id
    a, b, c = (dict(tight, row_id=i) for i in "abc")
    a["cost"] = dict(a["cost"], total_pp=3.0)
    b["cost"] = dict(b["cost"], total_pp=1.0)
    c["cost"] = dict(c["cost"], total_pp=1.0)
    got = A.rank([a, c, b])
    assert [r["row_id"] for r in got] == ["b", "c", "a"] and [r["rank"] for r in got] == [1, 2, 3]


def test_a_claim_with_no_price_on_its_side_is_not_a_row():
    assert brow("x", 0.6, 0.5, None, -110) is None


# ----------------------------------------------------------------- the band flag

def leans(k, n, gap, price_be=0.54, week=3):
    return [{"result": "cleared" if i < k else "missed", "gap_pp": gap, "breakeven": price_be,
             "season": 2026, "week": week} for i in range(n)]


def test_band_flag_is_computed_and_reaches_both_answers():
    low = {b["lo_pp"]: b for b in A.ledger_bands(leans(24, 68, 5.0) + leans(21, 38, -7.0))}
    assert low[4.0]["flagged"] is True and (low[4.0]["cleared"], low[4.0]["graded"]) == (24, 68)
    assert low[4.0]["interval"][1] < low[4.0]["breakeven"]
    assert low[6.0]["flagged"] is False            # 21 of 38: the interval spans break-even
    assert low[8.0] == {"lo_pp": 8.0, "hi_pp": None, "graded": 0, "cleared": None, "rate": None,
                        "interval": None, "interval_method": "wilson_95", "weeks": 0,
                        "breakeven": None, "priced": 0, "flagged": False}
    fine = {b["lo_pp"]: b for b in A.ledger_bands(leans(40, 68, 5.0))}
    assert fine[4.0]["flagged"] is False            # same band, other data, other answer
    # pushes and ungraded leans are not graded
    assert A.ledger_bands([{"result": "push", "gap_pp": 5.0, "season": 2026, "week": 3}])[0]["graded"] == 0


def test_flag_lands_on_book_props_in_a_flagged_band_only():
    live = A.ledger_bands(leans(24, 68, 5.0))
    prop = brow("books:p", 0.45, 0.50, -110, -110)                  # -5: in the 4-6 band
    big = brow("books:q", 0.30, 0.50, -110, -110)                   # -20: the 8+ band
    game = brow("books:g", 0.45, 0.50, -110, -110, market="spread")
    exch = xrow("kalshi:p", 0.45, 0.49, 0.51, market="prop")
    out = {r["row_id"]: r["flagged"] for r in A.apply_flags([prop, big, game, exch], live)}
    assert out == {"books:p": True, "books:q": False, "books:g": False, "kalshi:p": False}
    assert prop["gap_band"] == {"lo_pp": 4.0, "hi_pp": 6.0} == exch["gap_band"]
    assert not any(r["flagged"] for r in A.apply_flags([prop], A.ledger_bands(leans(40, 68, 5.0))))


# ----------------------------------------------------------------- the ordering sentence

def wf(top_roi_ci):
    return A.walkforward_bands({"bands": {
        "receptions|4-6": {"n": 10, "games": 9, "cleared": 0.5, "ci": [0.4, 0.6], "roi": -0.05,
                           "roi_ci": [-0.1, 0.01]},
        "receptions|8+": {"n": 10, "games": 9, "cleared": 0.55, "ci": [0.5, 0.6], "roi": 0.03,
                          "roi_ci": top_roi_ci}}})


def test_auc_words_reach_all_three_answers():
    assert "worse than" in A.auc_words([-0.043, -0.054, -0.033])["statement"]
    assert "better than" in A.auc_words([0.043, 0.033, 0.054])["statement"]
    assert "no better than" in A.auc_words([-0.01, -0.03, 0.01])["statement"]
    assert A.auc_words(None) is None


def test_ordering_block_can_say_larger_gaps_did_better_and_today_says_they_did_not():
    no = A.ordering_block([-0.043, -0.054, -0.033], wf([-0.02, 0.07]), [])
    yes = A.ordering_block([0.043, 0.033, 0.054], wf([0.01, 0.07]), [])
    assert no["larger_gap_has_meant_better"] is False
    assert yes["larger_gap_has_meant_better"] is True
    assert "0 of 2 gap bands" in no["statement"] and "1 of 2 gap bands" in yes["statement"]
    assert "of the 1 largest-gap bands, 1 did" in yes["statement"]
    assert no["statement"] != yes["statement"]
    empty = A.ordering_block(None, [], [])
    assert empty["statement"] is None and empty["larger_gap_has_meant_better"] is False


def test_the_committed_walkforward_bands_parse_and_none_clears_zero_today():
    doc = A.load_json(os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                   *A.BANDS_FILE.split("/")))
    bands = A.walkforward_bands(doc)
    assert len(bands) == len(doc["bands"]) >= 6
    assert {b["hi_pp"] for b in bands} == {6.0, 8.0, None}
    assert all(b["roi_verdict"] in ("above", "below", "contains_zero") for b in bands)


# ----------------------------------------------------------------- the file, the contract, the gate

def doc_of(rows, live=None):
    from jobs import export_web as E
    live = A.ledger_bands([]) if live is None else live
    rows = A.rank(A.apply_flags(rows, live))
    rec = {"file": "game/nfl/record.json", "generated_at": None, "beats": [], "beats_reason": "x",
           "vs_close": {"d_brier": {"estimate": 0.01, "interval": [0.0, 0.02], "se": 0.001,
                                    "verdict": "above"},
                        "compared": "worse than", "path": "market_comparison.d_brier",
                        "display": True},
           "published": None}
    return {A.KEY: {
        **E.envelope(A.KIND, E.iso(NOW), "nfl"), "season": 2026, "week": 5,
        "as_of": {"instant": A.iso(NOW), "kickoffs_after": A.iso(NOW)},
        "ranking": {"by": "net_pp", "order": "descending", "ties": ["cost.total_pp ascending"],
                    "is_a_finding": False, "ordering_note": A.ORDERING_NOTE},
        "ordering": A.ordering_block([-0.043, -0.054, -0.033], wf([-0.02, 0.07]), live),
        "gap_bands": live,
        "gap_bands_scope": {"population": "p", "flag_rule": "r", "flag_applies_to": "a",
                            "source": "s"},
        "records": {k: rec for k in {r["record"] for r in rows}},
        "parts": [{"id": p, "rows": 0, "reason": None, "dropped": {}} for p in A.PARTS],
        "rows": rows, "not_covered": [], "definitions": {"net_pp": "x"}}}


def test_the_file_passes_the_contract_the_source_gate_and_its_own_gate():
    from jobs import export_web as E
    files = doc_of([xrow("kalshi:a", 0.6, 0.49, 0.51), brow("books:b", 0.45, 0.5, -110, -110)])
    assert E.kind_for_key(A.KEY)[0] == A.KIND
    assert "2 rows" in A.gate(files)
    bad = json.loads(json.dumps(files))
    bad[A.KEY]["rows"][0]["pick"] = "over"                  # objects are closed
    with pytest.raises(Exception):
        E.validate_contract(bad)
    bad = json.loads(json.dumps(files))
    bad[A.KEY]["ranking"]["is_a_finding"] = True            # the sort is a sort, by const
    with pytest.raises(Exception):
        E.validate_contract(bad)


def test_the_gate_refuses_rows_out_of_order_and_a_net_that_is_not_gap_minus_cost():
    files = doc_of([xrow("kalshi:a", 0.6, 0.49, 0.51), xrow("kalshi:b", 0.7, 0.49, 0.51)])
    swapped = json.loads(json.dumps(files))
    swapped[A.KEY]["rows"].reverse()
    with pytest.raises(RuntimeError, match="rank"):
        A.gate(swapped)
    wrong = json.loads(json.dumps(files))
    wrong[A.KEY]["rows"][0]["net_pp"] += 1.0
    with pytest.raises(RuntimeError):
        A.gate(wrong)


def test_no_pick_language_in_the_file_or_its_contract_entry():
    files = doc_of([xrow("kalshi:a", 0.6, 0.49, 0.51), brow("books:b", 0.45, 0.5, -110, -110)])
    text = json.dumps(files).lower()
    for word in ("best bet", "units", "stars", "confident", "lock", "recommend", '"pick'):
        assert word not in text, word


# ----------------------------------------------------------------- non-fatal, previous kept

def empty_store(path):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE nfl_games (game_id TEXT, season INT, week INT, game_type TEXT, "
                "kickoff_ts REAL, home_team TEXT, away_team TEXT, gameday TEXT)")
    con.commit()
    con.close()
    return str(path)


def test_publish_never_raises_and_a_failed_build_leaves_the_previous_file(tmp_path):
    dest = tmp_path / "board"
    key = dest / "board" / "nfl" / "appealing.json"
    key.parent.mkdir(parents=True)
    key.write_text('{"previous": true}', encoding="utf-8")
    log = []
    out = A.publish(str(dest), web_dir=str(tmp_path / "web"), now_ts=NOW, log=log.append,
                    db=empty_store(tmp_path / "s.db"))
    assert out["written"] == 0 and out["built"] == [] and len(out["failed"]) == 1
    assert "NothingToRank" in out["failed"][0]["error"]
    assert key.read_text(encoding="utf-8") == '{"previous": true}'
    assert any("left in place" in line for line in log)
    # a store that cannot be opened at all is the same: logged, nothing raised
    out = A.publish(str(dest), web_dir=None, now_ts=NOW, log=log.append,
                    db=str(tmp_path / "missing.db"))
    assert out["failed"] and key.read_text(encoding="utf-8") == '{"previous": true}'


def test_the_job_owns_no_prefix_and_opens_the_store_read_only():
    import ast
    src = open(A.__file__, encoding="utf-8").read()
    calls = [n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call)
             and getattr(n.func, "attr", None) == "sync_keys"]
    assert len(calls) == 1 and isinstance(calls[0].args[2], ast.List) and not calls[0].args[2].elts
    assert "mode=ro" in src and "store.db(" not in src and "init_db" not in src
