"""The Lab library as static files (a-32). pytest -q tests/test_lab_publish.py

Every test builds its own universe in memory and writes under tmp_path; nothing
opens a store. What is asserted, and why each assertion can fail:

  - every launch preset is in the index, in order, published or refused WITH the
    engine's own reason - so the Library cannot silently miss one;
  - no book price leaves: no per-bet price, book, stake or profit, and the
    CONTRACT refuses a row carrying one (shown refusing, not just passing);
  - ROI is withheld where it would average fewer than MIN_CLEARED cleared prices,
    shown on both sides of the threshold;
  - `lab/` is owned by exactly one builder and its delete path is seen to fire;
  - the kinds' sources are derived from lab.universe's SQL, and the gate refuses
    them when that scan is taken away.
"""
import ast
import copy
import json
import os

import numpy as np
import polars as pl
import pytest

from jobs import export_web as E
from jobs import lab_publish as L
from jobs import source_registry as R
from lab import catalogue

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEN = "2026-09-24T20:00:00Z"
MARKETS = {"prop": ["receptions", "receiving_yards", "rush_attempts", "tackles_assists", "sacks"],
           "spread": ["spread"], "total": ["total"]}
# `books` is deliberately NOT here: strategy.price.books names the three books the
# consensus is taken over, which is the rule, not a per-bet price or book.
PRICE_FIELDS = {"price_american", "price_decimal", "decimal", "american", "book",
                "books_quoting", "stake", "profit", "chart", "drawdown"}


def _prop(season, week, subject, outcome, market="receptions", side="under", streak=0,
          american=-110, p=0.5):
    return {"bet_type": "prop", "market": market, "season": season, "week": week,
            "season_type": "REG", "game_id": "%d_%02d_%s" % (season, week, subject),
            "kickoff_ts": float(season * 1e6 + week * 1e4), "subject": subject,
            "event_subject": subject,
            "claim": "%d|%d|%s|%s|4.5" % (season, week, subject, market), "line": 4.5,
            "side": side, "team": "AAA", "opp": "BBB", "book": "draftkings",
            "american": float(american), "p_devig": p, "book_hold": 0.045,
            "source": "oddsapi_close", "outcome": outcome, "actual": 3.0,
            "game.home": True, "game.dome": False, "player.position": "WR",
            "player.streak": float(streak)}


def _spread(season, week, team, line, outcome):
    return {"bet_type": "spread", "market": "spread", "season": season, "week": week,
            "season_type": "REG", "game_id": "%d_%02d_%s" % (season, week, team),
            "kickoff_ts": float(season * 1e6 + week * 1e4), "subject": team,
            "event_subject": team, "claim": "%d_%02d_%s|spread|%g" % (season, week, team, line),
            "line": float(line), "side": "home", "team": team, "opp": "ZZZ",
            "book": "draftkings", "american": -110.0, "p_devig": 0.5, "book_hold": 0.045,
            "source": "oddsapi_close", "outcome": outcome, "actual": 0.0,
            "game.home": True, "game.dome": False, "player.position": None,
            "player.streak": None}


def _universe(rows, fixed=True):
    df = pl.DataFrame(rows, infer_schema_length=None)
    cov = {"prop": (2023, 2025), "spread": (1999, 2025), "total": (1999, 2025)}
    feats = catalogue.ranges(None, cov, MARKETS,
                             derive=lambda con, m: (1999, 2026, "fake survey"))
    return {"rows": df, "meta": {"features": feats, "price_coverage": cov,
                                 "latest_complete_season": 2025, "settlement_fixed": fixed,
                                 "built": "2026-09-24T17:23:59+00:00"}}


def _rows(rng, per_week=6):
    rows = []
    for y in (2023, 2024, 2025):
        for w in range(1, 11):
            for i in range(per_week):
                for side in ("over", "under"):
                    out = "cleared" if rng.random() < 0.5 else "missed"
                    rows.append(_prop(y, w, "P%02d" % i, out, side=side, streak=i % 5,
                                      market=("receptions", "rush_attempts")[i % 2]))
            rows.append(_spread(y, w, "T%02d" % w, 3.5 + (w % 4), "cleared" if w % 2 else "missed"))
    return rows


@pytest.fixture(scope="module")
def files():
    return L.build(_universe(_rows(np.random.default_rng(7))), GEN, log=lambda *_: None)


# ------------------------------------------------------------------ the launch set

def test_every_launch_preset_is_listed_in_order_published_or_refused_with_a_reason(files):
    idx = files[L.INDEX_KEY]
    keys = [p["key"] for p in idx["presets"]]
    assert keys == [p["key"] for p in L.PRESETS]
    assert len(keys) == 8
    status = {p["key"]: p for p in idx["presets"]}
    for k in ("fade_every_over", "rush_attempts_unders", "chase_the_streak",
              "home_underdogs_3_to_7"):
        assert status[k]["status"] == "published" and status[k]["file"] in files
    for k in ("model_leans", "buy_the_middle", "arbitrage_between_books", "unders_in_the_wind"):
        assert status[k]["status"] == "unsupported" and status[k]["file"] is None
        assert status[k]["reasons"], k
    assert "walk-forward" in " ".join(status["model_leans"]["reasons"])
    assert "forecast weather" in " ".join(status["unders_in_the_wind"]["reasons"])
    assert "R16" in status["buy_the_middle"]["register"]
    assert set(files) - {L.INDEX_KEY} == {p["file"] for p in idx["presets"] if p["file"]}


def test_the_files_satisfy_the_contract_and_the_source_gate(files):
    E.validate_contract(files)
    approved = R.require_declared(files)
    assert "oddsapi" in approved[("nfl", "lab_preset")]
    assert approved[("nfl", "lab_index")] == approved[("nfl", "lab_preset")]


def test_a_pre_fix_universe_is_refused():
    with pytest.raises(SystemExit, match="settlement fix"):
        L.build(_universe(_rows(np.random.default_rng(1)), fixed=False), GEN,
                log=lambda *_: None)


def test_the_results_are_the_engines_own(files):
    """The file is a projection of lab.run's result, not a second computation."""
    from lab import run
    u = _universe(_rows(np.random.default_rng(7)))
    preset = next(p for p in L.PRESETS if p["key"] == "fade_every_over")
    r = run(preset["strategy"], u)
    f = files["lab/nfl/presets/fade_every_over.json"]
    assert f["strategy_hash"] == r["strategy_hash"]
    assert f["summary"]["roi"]["value"] == r["summary"]["roi"]
    assert f["summary"]["roi"]["ci"] == [r["summary"]["roi_lo"], r["summary"]["roi_hi"]]
    assert f["summary"]["hit_rate"]["ci"] == [r["summary"]["hit_rate_lo"], r["summary"]["hit_rate_hi"]]
    assert f["bet_list"]["n"] == len(r["bet_list"]) == r["summary"]["bets"]
    assert f["holdout"] == {"season": 2025, "revealed": False, "note": r["holdout"]["note"]}
    assert all(row[1] != 2025 for row in f["bet_list"]["rows"])


# ------------------------------------------------------------------ no price leaves

def _keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _keys(v)


def test_no_price_book_stake_or_profit_field_anywhere(files):
    for key, f in files.items():
        leaked = PRICE_FIELDS & set(_keys(f))
        assert not leaked, (key, leaked)
    for key, f in files.items():
        if f["kind"] != "lab_preset":
            continue
        assert f["bet_list"]["columns"] == ["date", "season", "week", "game_id", "subject",
                                            "team", "opp", "market", "line", "side", "p_devig",
                                            "outcome", "actual"]
        assert all(len(r) == 13 for r in f["bet_list"]["rows"])
        assert "Deliberately restricted" in f["bet_list"]["restriction"]


def test_the_engine_result_DOES_carry_prices_so_the_projection_is_what_removes_them():
    """Discriminates the test above: the unprojected result has every field it bans."""
    from lab import run
    u = _universe(_rows(np.random.default_rng(7)))
    r = run(L.PRESETS[0]["strategy"], u)
    assert {"price_american", "price_decimal", "stake", "profit"} <= set(r["bet_list"][0])
    assert "chart" in r


def test_the_contract_refuses_a_price_column_on_a_bet_row_or_a_price_field(files):
    key = "lab/nfl/presets/fade_every_over.json"
    bad = copy.deepcopy(files[key])
    bad["bet_list"]["rows"][0] = bad["bet_list"]["rows"][0] + [-110]
    with pytest.raises(E.ContractError, match="bet_list/rows/0"):
        E.validate_contract({key: bad})
    bad = copy.deepcopy(files[key])
    bad["bet_list"]["columns"] = bad["bet_list"]["columns"] + ["price_american"]
    with pytest.raises(E.ContractError):
        E.validate_contract({key: bad})
    bad = copy.deepcopy(files[key])
    bad["summary"]["mean_price_american"] = -110
    with pytest.raises(E.ContractError, match="mean_price_american"):
        E.validate_contract({key: bad})
    bad = copy.deepcopy(files[key])
    bad["chart"] = {"units": [1, 2]}
    with pytest.raises(E.ContractError):
        E.validate_contract({key: bad})


def _cell(cleared, missed=5):
    return {"key": "x", "bets": cleared + missed, "weeks": 3, "cleared": cleared,
            "missed": missed, "push": 0, "void": 0, "hit_rate": cleared / (cleared + missed),
            "hit_rate_lo": 0.1, "hit_rate_hi": 0.9, "roi": -0.05, "roi_lo": -0.2,
            "roi_hi": 0.1, "thin": True}


def test_roi_is_withheld_below_min_cleared_and_published_at_it():
    below = L._cell(_cell(L.MIN_CLEARED - 1), 200)
    at = L._cell(_cell(L.MIN_CLEARED), 200)
    assert below["roi"] is None and "withheld" in below["withheld"]
    assert below["hit_rate"] is not None          # a count reveals no price
    assert at["roi"] is not None and at["withheld"] is None
    assert at["roi"]["blocks"] == 3 and at["roi"]["n"] == L.MIN_CLEARED + 5


def test_every_published_roi_averages_at_least_min_cleared_prices(files):
    seen = 0
    for f in files.values():
        if f["kind"] != "lab_preset":
            continue
        cells = [f["summary"]] + f["by_season"] + [c for v in f["segments"].values() for c in v]
        for c in cells:
            if c["roi"] is not None:
                seen += 1
                assert c["cleared"] >= L.MIN_CLEARED
            else:
                assert c["withheld"] or not (c["cleared"] + c["missed"])
    assert seen > 0


def test_a_summary_below_the_threshold_withholds_break_even_and_units_too():
    rows = [_prop(2023, w, "A", "cleared" if w < 4 else "missed") for w in range(1, 9)]
    rows += [_spread(2023, 1, "TTT", 4.5, "missed")]
    f = L.build(_universe(rows), GEN, log=lambda *_: None)
    s = f["lab/nfl/presets/fade_every_over.json"]["summary"]
    assert s["cleared"] == 3
    assert s["roi"] is None and s["units"] is None and s["break_even"] is None
    assert s["withheld"]
    E.validate_contract(f)


# ------------------------------------------------------------------ one copy of the strategy shape

def test_the_contracts_strategy_is_derived_from_lab_strategy_schema():
    with open(os.path.join(ROOT, "lab", "strategy.schema.json"), encoding="utf-8") as fh:
        strat = json.load(fh)
    got = E.CONTRACT["$defs"]["LabStrategy"]
    assert got == L.contract_strategy(strat)
    # the derivation changes exactly three things, and they are visible here
    assert strat["properties"]["sport"] != got["properties"]["sport"] == {"type": "string"}
    assert "maxLength" in strat["properties"]["name"] and "maxLength" not in got["properties"]["name"]
    sv = strat["properties"]["conditions"]["items"]["properties"]["value"]
    gv = got["properties"]["conditions"]["items"]["properties"]["value"]
    assert sv == {} and "anyOf" in gv
    a, b = copy.deepcopy(strat["properties"]), copy.deepcopy(got["properties"])
    for k in ("sport", "name"):
        a.pop(k), b.pop(k)
    a["conditions"]["items"]["properties"].pop("value")
    b["conditions"]["items"]["properties"].pop("value")
    assert a == b


def test_every_published_strategy_passes_the_strategy_schema_itself(files):
    """The contract dropped maxLength and the sport const; the producer still
    enforces both, because every strategy it publishes passed validate_shape."""
    from lab import strategy as S
    for f in files.values():
        if f["kind"] == "lab_preset":
            S.validate_shape(f["strategy"])
    with pytest.raises(S.Invalid):
        S.validate_shape(dict(files["lab/nfl/presets/fade_every_over.json"]["strategy"],
                              name="x" * 201))


def test_line_choice_branches_are_disjoint_so_anyof_equals_oneof():
    """lab/strategy.schema.json's line_choice was oneOf; it is anyOf because the
    site's generator cannot read oneOf. The two accept the same set only if no
    value matches two branches - checked on every shape each branch accepts."""
    import jsonschema
    with open(os.path.join(ROOT, "lab", "strategy.schema.json"), encoding="utf-8") as fh:
        branches = json.load(fh)["properties"]["line_choice"]["anyOf"]
    for v in ("main", "all_rungs", "nearest_to(4.5)", "nearest_to(-3)", {"nearest_to": 4.5}):
        hits = sum(jsonschema.Draft202012Validator(b).is_valid(v) for b in branches)
        assert hits == 1, (v, hits)


# ------------------------------------------------------------------ the prefix

def _sync_calls(path):
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and (getattr(n.func, "attr", None) == "sync_keys"
                                        or getattr(n.func, "id", None) == "sync_keys"):
            out.append(ast.literal_eval(n.args[2]))
    return out


def test_lab_is_owned_by_this_builder_alone():
    assert _sync_calls(os.path.join(ROOT, "jobs", "lab_publish.py")) == [["lab/"]]
    from tests.test_prefix_ownership import owned_prefixes
    for mod in ("export_web.py", "board_read.py", "export_cfb_web.py", "export_mlb_web.py"):
        path = os.path.join(ROOT, "jobs", mod)
        if not os.path.exists(path):
            continue
        prefixes, dynamic = owned_prefixes(path)
        assert dynamic == 0, mod
        for p in prefixes:
            assert not ("lab/".startswith(p) or p.startswith("lab/")), (mod, p)


def test_publish_deletes_a_stale_lab_key_and_nothing_outside_lab(tmp_path, files):
    u = _universe(_rows(np.random.default_rng(7)))
    stale = tmp_path / "lab" / "nfl" / "presets" / "retired_preset.json"
    other = tmp_path / "nfl" / "manifest.json"
    for p in (stale, other):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}", encoding="utf-8")
    out = L.publish(u, str(tmp_path), generated_at=GEN, log=lambda *_: None)
    assert out["deleted"] == 1 and not stale.exists() and other.exists()
    assert out["published"] == 4 and out["unsupported"] == 4
    again = L.publish(u, str(tmp_path), generated_at="2026-09-25T00:00:00Z", log=lambda *_: None)
    assert again["written"] == 0            # generated_at alone is not a change
    chk = L.check_tree(str(tmp_path), log=lambda *_: None)
    assert chk["keys"] == 5 and chk["bytes"] > 0


def test_check_tree_refuses_an_empty_tree_and_an_index_that_disagrees(tmp_path):
    with pytest.raises(SystemExit, match="nothing to check"):
        L.check_tree(str(tmp_path), log=lambda *_: None)
    u = _universe(_rows(np.random.default_rng(7)))
    L.publish(u, str(tmp_path), generated_at=GEN, log=lambda *_: None)
    os.remove(tmp_path / "lab" / "nfl" / "presets" / "chase_the_streak.json")
    with pytest.raises(E.ContractError, match="chase_the_streak"):
        L.check_tree(str(tmp_path), log=lambda *_: None)


# ------------------------------------------------------------------ the sources are derived

def test_lab_sources_are_derived_from_the_universe_sql_and_refused_without_it(monkeypatch, files):
    decl = R.DECLARED["nfl"]["lab_preset"]
    assert {"oddsapi", "nflverse.stats", "nflverse.schedule", "nflverse.snap_counts"} <= set(decl)
    assert not {"kalshi.ladders", "polymarket"} & set(decl)     # NARROW: Odds API closes only
    monkeypatch.setitem(R.SIDE_PRODUCERS, "nfl", ("jobs.board_read",))
    try:
        R.refresh()
        with pytest.raises(R.SourceRegistryError, match="lab_preset"):
            R.require_declared(files)
    finally:
        monkeypatch.undo()
        R.refresh()
    assert R.require_declared(files)


# ------------------------------------------------------------------ engine additions

def test_a_rule_whose_slice_is_empty_reports_no_bets_rather_than_crashing():
    """Found by this unit: `_price` returned the bare empty slice, which has no
    `decimal` column, and the rule crashed with ColumnNotFoundError."""
    from lab import run
    u = _universe([_prop(2023, 1, "A", "cleared", market="receptions")])
    s = {"schema": "lab.strategy/1", "sport": "nfl", "bet_type": "prop",
         "markets": ["rush_attempts"], "seasons": {"from": 2023, "to": 2025}, "side": "under",
         "conditions": [{"feature": "price.line", "op": ">", "value": 1}]}
    r = run(s, u)
    assert r["verdict"] == "NO BETS" and r["summary"]["bets"] == 0 and r["bet_list"] == []


def test_units_interval_and_per_cell_wilson_come_from_the_engine():
    from lab import run
    u = _universe(_rows(np.random.default_rng(3)))
    r = run(L.PRESETS[0]["strategy"], u)
    s = r["summary"]
    assert s["units_lo"] <= s["units"] <= s["units_hi"]
    # flat 1-unit stakes: units is profit, so the units interval is the profit interval
    assert s["units_lo"] < s["units_hi"]
    for c in r["by_season"]:
        assert c["hit_rate_lo"] <= c["hit_rate"] <= c["hit_rate_hi"]
        assert c["cleared"] + c["missed"] + c["push"] + c["void"] == c["bets"]
        assert c["weeks"] >= 1
    assert {"team", "opp"} <= set(r["bet_list"][0])
