"""a-80: which markets the model prices, and the ledger prices that are not prices.

Run: pytest -q tests/test_a80_markets_and_prices.py

1. THE INDEX SAYS WHICH MARKETS THE MODEL PRICES. Before this a page inferred
   "not modelled" from a read in which no line of a market priced - so a read in
   which every fit of a MODELLED market failed would have been worded as a market
   the model does not price. Both reads are built here through the real job and
   shown to publish different states.
2. A PRICE STRICTLY BETWEEN -100 AND +100 IS NOT AN AMERICAN PRICE. The lean-side
   price is the median of the books' American prices; two books on opposite
   sides of even money give their mean (+112 and -105 -> 3.5). It is withheld on
   the row and on the ledger, the writer refuses one that arrives anyway, and
   every valid median is exactly what it was.

   WHAT THIS GATE DOES NOT COVER, said here so a green run is not misread: the
   published leans that carried such a number before a-80 (17 on 2026-10-08) are
   in a ledger no row of which is ever rewritten. They keep it, and their
   terminal rows copy it. `test_a_grandfathered_lean_is_graded_at_the_price_it_was_published_at`
   is that exemption, and it is an exemption.
"""
import json
import os

import pytest
from jsonschema import Draft202012Validator

import config
from core import board as B
from jobs import export_web as E
from jobs import source_registry as R
from tests.test_board import H, K_DET, K_GB, T0, _row, env, ledger, read, snapshot  # noqa: F401

DEFS = E.CONTRACT["$defs"]
GB, DET = "ev-2026_03_ATL_GB", "ev-2026_03_NYJ_DET"
MSG = "lean price withheld: median of the books is not an American price"


@pytest.fixture(autouse=True)
def _fresh_runtime_ledger():
    yield
    R._READS.pop("nfl", None)


def errors(name, obj):
    v = Draft202012Validator({"$ref": f"#/$defs/{name}", "$defs": DEFS})
    return [e.message for e in v.iter_errors(obj)]


def index_and_read(env):
    wd = env["J"].week_dir(env["dest"], 2026, 3)
    idx = json.load(open(os.path.join(wd, "index.json")))
    doc = json.load(open(os.path.join(wd, env["J"].read_name(idx["latest"]))))
    return idx, doc


def by_market(idx):
    return {m["market"]: m for m in idx["model_markets"]}


# ================================================================== 1. the three states

def test_the_three_states_and_the_counts_behind_them():
    rows = ([{"market": "receptions", "model_p_over": 0.6}] * 3
            + [{"market": "receptions", "model_p_over": None}]
            + [{"market": "rush_attempts", "model_p_over": None}] * 2
            + [{"market": "receiving_yards", "model_p_over": None}] * 5)
    got = B.model_markets(rows, ("receptions", "rush_attempts", "receiving_yards", "sacks"),
                          ("receptions", "rush_attempts", "sacks"))
    assert got == [
        # one line failed, the market did not
        {"market": "receptions", "state": B.M_PRICED, "lines": 4, "priced": 3, "fit_failed": 1},
        # a specification, lines listed, none priced: the fit failed - NOT "not modelled"
        {"market": "rush_attempts", "state": B.M_FIT_FAILED, "lines": 2, "priced": 0, "fit_failed": 2},
        # no specification: nothing was attempted, so nothing failed
        {"market": "receiving_yards", "state": B.M_NOT_MODELLED, "lines": 5, "priced": 0, "fit_failed": 0},
        # a specification and no line listed yet: nothing attempted, nothing failed
        {"market": "sacks", "state": B.M_PRICED, "lines": 0, "priced": 0, "fit_failed": 0},
    ]
    for entry in got:
        assert errors("BoardModelMarket", entry) == []
    # exactly three states exist
    assert {B.M_PRICED, B.M_FIT_FAILED, B.M_NOT_MODELLED} == set(
        DEFS["BoardModelMarket"]["properties"]["state"]["enum"])


def test_a_market_with_rows_is_listed_even_when_the_config_no_longer_names_it():
    got = B.model_markets([{"market": "sacks", "model_p_over": None}], ("receptions",), ("receptions",))
    assert [(m["market"], m["state"], m["lines"]) for m in got] == [
        ("receptions", B.M_PRICED, 0), ("sacks", B.M_NOT_MODELLED, 1)]


def test_a_null_model_price_in_a_modelled_market_can_only_be_a_failed_fit(monkeypatch):
    """The claim the three states rest on, checked on the real `model_prob` (the
    scenario fixture replaces it): outside BOARD_MODEL_STATS it returns null and
    ATTEMPTS NOTHING; inside, null only when the fit raised."""
    from jobs import board_read as J
    from models import baseline
    calls = []

    def boom(*a, **k):
        calls.append(a)
        raise RuntimeError("no prior games")
    monkeypatch.setattr(baseline, "fit_player_stat", boom)
    monkeypatch.setattr(config, "BOARD_MODEL_STATS", ("receptions",))
    cache = {}
    assert J.model_prob(None, "g", "receiving_yards", 2026, T0, "WR", "DET", 55.5, cache) is None
    assert calls == [] and cache == {}                       # never attempted
    assert J.model_prob(None, "g", "receptions", 2026, T0, "WR", "DET", 4.5, cache) is None
    assert len(calls) == 1 and isinstance(cache[("g", "receptions")], RuntimeError)   # attempted, failed


def _one_read(env):
    snapshot(T0 - H, GB, [("player_receptions", "Kyle Pitts", 3.5, -110, -110, None),
                          ("player_receptions", "Drake London", 5.5, -110, -110, None),
                          ("player_rush_attempts", "Bijan Robinson", 17.5, -110, -110, None)])
    return read(env, T0)


def test_the_index_publishes_the_market_list_and_it_is_the_reads_rows(env):
    _rows, idx = _one_read(env)
    m = by_market(idx)
    assert list(m) == list(config.BOARD_MARKETS.values())    # every market the Board lists
    assert m["receptions"] == {"market": "receptions", "state": "modelled_priced",
                               "lines": 2, "priced": 2, "fit_failed": 0}
    # the fixture's "model missing for a market": no specification for rush attempts
    assert m["rush_attempts"] == {"market": "rush_attempts", "state": "not_modelled",
                                  "lines": 1, "priced": 0, "fit_failed": 0}
    assert m["receiving_yards"]["state"] == "not_modelled" and m["receiving_yards"]["lines"] == 0
    assert "model_markets" in idx["definitions"]
    idx2, doc = index_and_read(env)
    assert errors("BoardIndexFile", idx2) == []
    assert "agree" in B.index_reconciles(idx2, doc)


def test_every_fit_failing_is_not_the_market_being_unmodelled(env, monkeypatch):
    """b-106's wrong sentence, at its source. The SAME rows - one rush-attempts
    line, no model price on it - publish a different state when the model has a
    specification for the market: the read can no longer be misread."""
    monkeypatch.setattr(config, "BOARD_MODEL_STATS", ("receptions", "rush_attempts"))
    rows, idx = _one_read(env)
    assert rows["00-BR:rush_attempts"]["model_p_over"] is None             # same null on the row
    assert by_market(idx)["rush_attempts"] == {"market": "rush_attempts",
                                               "state": "modelled_fit_failed",
                                               "lines": 1, "priced": 0, "fit_failed": 1}
    assert env["J"].check_tree(env["dest"], log=lambda *_: None)["json"] == 2


@pytest.mark.parametrize("plant, match", [
    (lambda m: m[0].update(lines=m[0]["lines"] + 1), "index says"),
    (lambda m: m[0].update(priced=0), "index says"),
    (lambda m: m.pop(1), "omits market"),
    (lambda m: m.append(dict(m[0])), "more than once"),
])
def test_a_market_list_that_is_not_the_reads_rows_is_refused(env, plant, match):
    _one_read(env)
    idx, doc = index_and_read(env)
    J = env["J"]
    ik, rk = J.index_key(2026, 3), J.read_key(2026, 3, idx["latest"])
    E.validate_contract({ik: idx, rk: doc})                  # the untampered pair passes
    plant(idx["model_markets"])
    with pytest.raises(E.ContractError, match=match):
        E.validate_contract({ik: idx, rk: doc})


def test_a_failed_fit_count_that_is_not_the_reads_is_refused(env, monkeypatch):
    monkeypatch.setattr(config, "BOARD_MODEL_STATS", ("receptions", "rush_attempts"))
    _one_read(env)
    idx, doc = index_and_read(env)
    J = env["J"]
    by_market(idx)["receptions"]["fit_failed"] = 1           # schema-legal, and false
    assert errors("BoardIndexFile", idx) == []
    with pytest.raises(E.ContractError, match="failed fits"):
        E.validate_contract({J.index_key(2026, 3): idx, J.read_key(2026, 3, idx["latest"]): doc})


def test_the_key_is_optional_and_its_shape_is_closed(env):
    _one_read(env)
    idx, doc = index_and_read(env)
    J = env["J"]
    old = {k: v for k, v in idx.items() if k != "model_markets"}
    old["definitions"] = {k: v for k, v in idx["definitions"].items() if k != "model_markets"}
    # an index written before a-80 still validates and still reconciles
    E.validate_contract({J.index_key(2026, 3): old, J.read_key(2026, 3, idx["latest"]): doc})
    assert "model_markets" not in DEFS["BoardIndexFile"]["required"]
    good = {"market": "receptions", "state": "modelled_priced", "lines": 2, "priced": 2, "fit_failed": 0}
    assert errors("BoardModelMarket", good) == []
    for bad in (dict(good, state="unknown"),                                   # no fourth state
                dict(good, why="because"),                                     # closed
                {k: v for k, v in good.items() if k != "fit_failed"},
                dict(good, state="not_modelled", fit_failed=1),                # nothing attempted
                dict(good, state="modelled_fit_failed", fit_failed=2),         # priced 2 and "none priced"
                dict(good, state="modelled_fit_failed", lines=0, priced=0, fit_failed=0)):
        assert errors("BoardModelMarket", bad), bad


# ================================================================== 2. the price

@pytest.mark.parametrize("price, kept", [(-110, True), (100, True), (-100, True), (250.0, True),
                                         (-115.0, True), (99.99, False), (-99.99, False),
                                         (3.5, False), (-4.0, False), (0, False), (0.5, False)])
def test_an_american_price_is_at_or_beyond_a_hundred(price, kept):
    assert B.recordable_price(price) == (price if kept else None)
    assert B.recordable_price(None) is None


def _williams(env, quotes):
    """Williams 3.5 receptions with each book's own (over, under); the fixture's
    model says 0.62, so the lean is the over."""
    for book, (over, under) in quotes.items():
        snapshot(T0 - H, DET, [("player_receptions", "Jameson Williams", 3.5, over, under, [book])])
    summaries = []
    env["J"].run(2026, 3, env["dest"], read_ts=T0, log=summaries.append)
    idx, doc = index_and_read(env)
    row = next(r for r in doc["rows"] if r["gsis_id"] == "00-JW")
    pub = [e for e in ledger(env) if e["event"] == "published" and e["gsis_id"] == "00-JW"]
    assert row["lean"] == "over" and len(pub) == 1
    return row, pub[0], json.loads(summaries[-1])["fresh"]


def test_two_books_either_side_of_even_money_publish_no_price(env):
    """The cause of all 17 on the published ledger: -105 and +112, mean 3.5."""
    row, pub, counts = _williams(env, {"draftkings": (-105, -115), "fanduel": (112, -140)})
    assert [b["over"] for b in row["books"]] == [-105, 112]  # the books are still shown as quoted
    assert row["lean_price"] is None and pub["price"] is None
    assert counts[MSG] == 1
    # the lean itself is untouched: published, on its line, at the same gap
    assert pub["side"] == "over" and pub["line"] == 3.5 and pub["gap_pp"] == row["gap_pp"]


@pytest.mark.parametrize("quotes, median", [
    ({"draftkings": (-105, -115), "fanduel": (112, -140), "betmgm": (-110, -110)}, -105),  # 3 books: a real quote
    ({"draftkings": (-110, -110), "fanduel": (-120, 100)}, -115),      # 2 books, same side: the mean, as before
    ({"draftkings": (104, -125), "fanduel": (112, -140)}, 108),
    ({"draftkings": (-105, -115)}, -105),
])
def test_every_valid_median_is_exactly_what_it_was(env, quotes, median):
    row, pub, counts = _williams(env, quotes)
    assert row["lean_price"] == median and pub["price"] == median
    assert MSG not in counts


def test_the_ledger_event_withholds_it_whatever_row_it_is_built_from():
    ev = B.ledger_events([], [dict(_row(), lean_price=-4.0)], "r1", lambda e: None, {}, "m", 4.0)
    assert len(ev) == 1 and ev[0]["price"] is None           # published all the same
    ok = B.ledger_events([], [dict(_row(), lean_price=-146.0)], "r1", lambda e: None, {}, "m", 4.0)
    assert ok[0]["price"] == -146.0


def _published(price, claim="G:P:receptions"):
    """A published event as it sits in a ledger written BEFORE a-80."""
    ev = B.ledger_events([], [_row(claim=claim)], "2026-09-26T19:19:46Z", lambda e: None, {}, "m", 4.0)[0]
    return dict(ev, price=price)


def test_the_writer_refuses_a_new_published_row_with_such_a_price(env):
    J, dest = env["J"], env["dest"]
    with pytest.raises(E.ContractError, match="not an American price"):
        J.write_ledger(dest, [], [_published(-4.0)])
    assert not os.path.exists(J.ledger_path(dest))           # nothing appended
    assert J.write_ledger(dest, [], [_published(-146.0)]) == 1


def _old_ledger_with_a_gap_price(env, monkeypatch):
    """A ledger on disk holding a lean published at 3.5 - what production has."""
    J, dest = env["J"], env["dest"]
    with monkeypatch.context() as m:
        m.setattr(B, "price_gap_events", lambda old, new: [])    # the pre-a-80 writer
        J.write_ledger(dest, [], [_published(3.5)])
    return J, dest, J.read_ledger(dest)


def test_a_grandfathered_lean_is_graded_at_the_price_it_was_published_at(env, monkeypatch):
    """THE EXEMPTION, and its edge. A lean published before a-80 with 3.5 keeps
    it, and its graded row is a copy carrying 3.5 - refusing that would stop the
    week being graded. Nothing else passes: not the same lean at another such
    price, not a second published lean."""
    J, dest, old = _old_ledger_with_a_gap_price(env, monkeypatch)
    assert old[0]["price"] == 3.5
    graded = B.ledger_events(old, [], "2026-09-28T12:00:00Z",
                             lambda e: ("over", 5.0, None), {}, "m", 4.0)
    assert [e["event"] for e in graded] == ["graded"] and graded[0]["price"] == 3.5
    assert B.price_gap_events(old, graded) == []
    assert B.price_gap_events(old, [dict(graded[0], price=-4.0)]) != []     # not ITS price
    assert B.price_gap_events(old, [_published(3.5, claim="G:Q:receptions")]) != []
    with pytest.raises(E.ContractError, match="not an American price"):
        J.write_ledger(dest, old, [_published(3.5, claim="G:Q:receptions")])
    assert len(J.read_ledger(dest)) == 1                     # the refusal wrote nothing
    assert J.write_ledger(dest, old, graded) == 1
    rows = J.read_ledger(dest)
    assert [r["price"] for r in rows] == [3.5, 3.5]
    B.assert_append_only(old, rows)                          # the old row was not touched


def test_no_new_published_row_in_a_written_ledger_is_in_the_gap(env):
    """The gate as the brief words it, over a ledger the real job writes across
    two reads with a straddling pair in each."""
    snapshot(T0 - H, DET, [("player_receptions", "Jameson Williams", 3.5, -105, -115, ["draftkings"]),
                           ("player_receptions", "Jameson Williams", 3.5, 112, -140, ["fanduel"]),
                           ("player_receptions", "Amon-Ra St. Brown", 7.5, -102, -120, ["draftkings"]),
                           ("player_receptions", "Amon-Ra St. Brown", 7.5, 103, -125, ["betmgm"])])
    read(env, T0)
    snapshot(T0 + H, GB, [("player_receptions", "Kyle Pitts", 3.5, -128, 100, ["draftkings"]),
                          ("player_receptions", "Kyle Pitts", 3.5, 100, -128, ["betmgm"])])
    read(env, T0 + 2 * H)
    led = ledger(env)
    pubs = [e for e in led if e["event"] == "published"]
    assert len(pubs) == 3                                    # the gate is not vacuous
    assert [e for e in led if e["price"] is not None and -100 < e["price"] < 100] == []
    assert [e["price"] for e in pubs] == [None, None, None]
