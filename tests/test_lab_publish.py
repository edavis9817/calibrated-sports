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
# `drawdown` left this set in a-44: its size is now published, but only under
# `verify_drawdown` (tested below). What stays banned is anything bet-level:
# the engine's bet positions (`members`, `peak_index`, `trough_index`) and its
# bet-granularity band (`band_at` is published only as `luck.paths`).
PRICE_FIELDS = {"price_american", "price_decimal", "decimal", "american", "book",
                "books_quoting", "stake", "profit", "chart", "members", "peak_index",
                "trough_index", "band_at"}


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
    assert (set(files) - {L.INDEX_KEY, L.CATALOGUE_KEY}
            == {p["file"] for p in idx["presets"] if p["file"]})


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
    assert chk["keys"] == 6 and chk["bytes"] > 0


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



# ------------------------------------------------------------------ the catalogue (a-45)

def test_the_catalogue_lists_every_feature_in_the_catalogue_once_in_order(files):
    cat = files[L.CATALOGUE_KEY]
    assert cat["kind"] == "lab_catalogue"
    keys = [f["key"] for f in cat["features"]]
    assert keys == [f.key for f in catalogue.FEATURES]
    assert set(keys) == set(catalogue.BY_KEY) and len(keys) == len(set(keys))
    by = {f["key"]: f for f in cat["features"]}
    for k, f in catalogue.BY_KEY.items():
        assert (by[k]["label"], by[k]["group"], by[k]["value_type"], by[k]["bet_types"]) == \
            (f.label, f.group, f.dtype, list(f.bet_types))


def test_every_unavailable_feature_carries_a_reason_and_no_available_one_does(files):
    feats = files[L.CATALOGUE_KEY]["features"]
    rows = feats + [m for f in feats for m in (f["by_market"] or [])]
    declared = {f.key for f in catalogue.FEATURES if f.availability == "none"}
    assert len(declared) == 6
    assert declared <= {r["key"] for r in feats if r["availability"] == "none"}
    none = [r for r in rows if r["availability"] == "none"]
    assert len(none) >= 6
    for r in none:
        assert isinstance(r["reason"], str) and r["reason"].strip(), r
        assert r["season_from"] is None and r["season_to"] is None and r["bound_from"] is None
    for r in rows:
        if r["availability"] == "historical":
            assert r["reason"] is None and r["season_from"] <= r["season_to"]
            assert r["bound_from"] and r["bound_to"]


def test_the_contract_refuses_an_unavailable_feature_with_an_empty_reason(files):
    E.validate_contract(files)
    bad = copy.deepcopy(files[L.CATALOGUE_KEY])
    next(x for x in bad["features"] if x["availability"] == "none")["reason"] = ""
    with pytest.raises(E.ContractError):
        E.validate_contract({L.CATALOGUE_KEY: bad})


def test_the_published_ranges_are_the_universes_own_and_name_the_price_bound(files):
    u = _universe(_rows(np.random.default_rng(7)))
    by = {f["key"]: f for f in files[L.CATALOGUE_KEY]["features"]}
    for k, d in u["meta"]["features"].items():
        if d["availability"] != "none":
            assert (by[k]["season_from"], by[k]["season_to"]) == (d["season_from"], d["season_to"])
            assert by[k]["note"] == d["note"]
    s = by["player.streak"]
    assert (s["season_from"], s["season_to"]) == (2023, 2025)
    assert s["price_seasons"] == {"from": 2023, "to": 2025}
    assert s["bound_from"] == s["bound_to"] == {"by": "prices", "columns": []}
    assert {m["market"]: m["availability"] for m in s["by_market"]}["receptions"] == "historical"
    assert by["game.home"]["price_seasons"] == {"from": 1999, "to": 2025}
    assert by["game.home"]["by_market"] is None and by["game.home"]["inputs"] == []


def _survey(monkeypatch, starts):
    """Drive the REAL derive_range: `starts` is {column: first usable season}."""
    from analytics import metrics
    monkeypatch.setattr(metrics, "live_season", lambda con: 2026)
    monkeypatch.setattr(metrics.survey, "coverage",
                        lambda con, column, dataset="", condition="":
                        [(y,) for y in range(starts.get(column, 1999), 2027)])
    monkeypatch.setattr(metrics.survey, "anomalies", lambda con, **kw: {})
    monkeypatch.setattr(metrics.survey, "silent_zeros", lambda con, **kw: [])
    return metrics.derive_range


def _meta(monkeypatch, starts, cov):
    feats = catalogue.ranges(object(), cov, MARKETS, derive=_survey(monkeypatch, starts))
    return {"features": feats, "price_coverage": cov, "built": "2026-09-24T17:23:59+00:00"}


EARLY = {"prop": (2005, 2025), "spread": (1999, 2025), "total": (1999, 2025)}


def test_an_input_bound_range_names_its_binding_column_from_the_real_derive_range(monkeypatch):
    # prices from 2005: snap counts (2013) and the target share (2009) now bind
    cat = L.catalogue_file(_meta(monkeypatch, {"offense_pct": 2013, "target_share": 2009,
                                               "def_tackles_with_assist": 2011}, EARLY))
    by = {f["key"]: f for f in cat["features"]}
    snap = by["player.snap_share_l3"]
    assert snap["season_from"] == 2013
    assert snap["bound_from"] == {"by": "inputs", "columns": ["snap_counts.offense_pct"]}
    assert snap["bound_to"] == {"by": "prices", "columns": []}
    assert by["player.target_share_l3"]["bound_from"] == {
        "by": "inputs", "columns": ["weekly_stats.target_share"]}
    # per market: tackles is bound by one of its three columns, receptions by the
    # prices; the feature-level start is the widest (2005), so the prices bind it
    streak = by["player.streak"]
    per = {m["market"]: m for m in streak["by_market"]}
    assert per["tackles_assists"]["bound_from"] == {
        "by": "inputs", "columns": ["weekly_stats.def_tackles_with_assist"]}
    assert per["receptions"]["bound_from"]["by"] == "prices"
    assert streak["season_from"] == 2005 and streak["bound_from"]["by"] == "prices"


def test_a_feature_level_input_bound_is_carried_from_the_markets_that_set_it(monkeypatch):
    late = {c: 2010 for c in ("receptions", "receiving_yards", "carries", "def_sacks")}
    cat = L.catalogue_file(_meta(monkeypatch, {**late, "def_tackles_with_assist": 2012}, EARLY))
    streak = next(f for f in cat["features"] if f["key"] == "player.streak")
    assert streak["season_from"] == 2010
    assert streak["bound_from"] == {"by": "inputs", "columns": [
        "weekly_stats.carries", "weekly_stats.def_sacks", "weekly_stats.receiving_yards",
        "weekly_stats.receptions"]}


def test_an_input_bound_end_whose_note_names_no_input_is_refused(monkeypatch):
    meta = _meta(monkeypatch, {"offense_pct": 2013}, EARLY)
    meta["features"]["player.snap_share_l3"]["note"] = "prices 2005-2025; survey: reworded"
    with pytest.raises(SystemExit, match="unexplained range"):
        L.catalogue_file(meta)


def test_a_universe_built_by_another_catalogue_or_without_the_survey_is_refused():
    from lab.universe import NO_SURVEY, _no_survey
    u = _universe(_rows(np.random.default_rng(7)))
    stale = copy.deepcopy(u["meta"])
    del stale["features"]["game.week"]
    with pytest.raises(SystemExit, match="game.week"):
        L.catalogue_file(stale)
    relabel = copy.deepcopy(u["meta"])
    relabel["features"]["game.week"]["bet_types"] = ["spread"]
    with pytest.raises(SystemExit, match="rebuild the universe"):
        L.catalogue_file(relabel)
    nosurvey = dict(u["meta"], features=catalogue.ranges(
        None, u["meta"]["price_coverage"], MARKETS, derive=_no_survey))
    assert NO_SURVEY in nosurvey["features"]["player.snap_share_l3"]["note"]
    with pytest.raises(SystemExit, match="column survey"):
        L.catalogue_file(nosurvey)


def test_the_catalogue_is_written_every_run_and_check_tree_requires_it(tmp_path):
    u = _universe(_rows(np.random.default_rng(7)))
    L.publish(u, str(tmp_path), generated_at=GEN, log=lambda *_: None)
    path = tmp_path / "lab" / "nfl" / "catalogue.json"
    assert path.exists()
    os.remove(path)
    with pytest.raises(E.ContractError, match="catalogue"):
        L.check_tree(str(tmp_path), log=lambda *_: None)
    out = L.publish(u, str(tmp_path), generated_at=GEN, log=lambda *_: None)
    assert out["written"] == 1 and path.exists()


def test_the_catalogue_sources_add_the_survey_to_the_universes(files):
    approved = R.require_declared(files)
    cat, preset = set(approved[("nfl", "lab_catalogue")]), set(approved[("nfl", "lab_preset")])
    assert preset <= cat and {"oddsapi", "nflverse.stats", "nflverse.snap_counts"} <= cat


# ------------------------------------------------------------------ the returns series (a-44)

def _engine(key, universe=None):
    from lab import run
    u = universe or _universe(_rows(np.random.default_rng(7)))
    preset = next(p for p in L.PRESETS if p["key"] == key)
    return run(preset["strategy"], u, series_min_cleared=L.MIN_CLEARED)


def _published_steps_against_bets(f, bet_list):
    """Map each published step to the server-side bets it covers, using ONLY the
    published fields (season, week_from, week_to) - not the engine's members."""
    return [[b for b in bet_list if b["season"] == st["season"]
             and st["week_from"] <= b["week"] <= st["week_to"]]
            for st in f["returns"]["steps"]]


def test_no_consecutive_published_points_difference_back_to_fewer_than_min_cleared(files, big):
    """THE property the series exists under: between any two consecutive published
    points - the origin included - sit at least MIN_CLEARED cleared bets, so the
    difference averages that many prices. Re-derived from the engine's own bet
    list (which carries the prices and profits) against the published steps."""
    checked = 0
    small = _universe(_rows(np.random.default_rng(7)))
    large = _universe(_rows(np.random.default_rng(11), per_week=30))
    for tree, u, key in [(t, u, k) for t, u in ((files, small), (big, large))
                         for k in ("fade_every_over", "rush_attempts_unders",
                                   "chase_the_streak", "home_underdogs_3_to_7")]:
        f = tree["lab/nfl/presets/%s.json" % key]
        bl = _engine(key, u)["bet_list"]
        steps = f["returns"]["steps"]
        if f["returns"]["withheld"]:
            assert steps == []
            continue
        covered = _published_steps_against_bets(f, bl)
        assert sum(len(c) for c in covered) == len(bl)        # every bet, exactly once
        prev = 0.0
        for st, bets in zip(steps, covered):
            cleared = sum(1 for b in bets if b["outcome"] == "cleared")
            assert cleared >= L.MIN_CLEARED, (key, st)
            assert cleared == st["cleared"] and len(bets) == st["bets"]
            # the difference IS the step's profit - so it averages `cleared` prices
            assert st["units"] - prev == pytest.approx(sum(b["profit"] for b in bets),
                                                       abs=1e-3 + 5e-5 * len(bets))
            prev = st["units"]
            checked += 1
        assert prev == pytest.approx(f["summary"]["units"]["value"], abs=2e-3)
    assert checked > 0


def test_steps_never_cross_a_season_and_thin_weeks_are_merged_not_dropped(files):
    f = files["lab/nfl/presets/fade_every_over.json"]
    r = f["returns"]
    assert r["granularity"] == "week_block" and r["min_cleared"] == L.MIN_CLEARED
    # ~3 cleared a week in the fixture, so weeks MUST merge - the rule is seen firing
    assert r["merged"] > 0 and r["withheld"] is None
    assert sum(s["weeks"] for s in r["steps"]) == r["week_blocks"]
    by_season = {}
    for s in r["steps"]:
        by_season.setdefault(s["season"], []).append(s)
    for y, ss in by_season.items():
        assert ss[0]["week_from"] == 1
        for a, b in zip(ss, ss[1:]):
            assert b["week_from"] == a["week_to"] + 1       # contiguous, within the season
    # each season's steps add up to its season cell: nothing straddles
    for cell in f["by_season"]:
        assert sum(s["bets"] for s in by_season[int(cell["key"])]) == cell["bets"]
    assert "none crosses a season" in r["check"]


def test_a_season_below_the_floor_withholds_the_whole_series_and_the_band():
    """A thin season cannot be merged anywhere: any point after it, less the one
    before it and the neighbouring season cell, is that season's profit."""
    rng = np.random.default_rng(3)
    rows = []
    for w in range(1, 11):                         # 2023: plenty
        for i in range(8):
            rows.append(_prop(2023, w, "P%02d" % i,
                              "cleared" if rng.random() < .5 else "missed"))
            rows.append(_prop(2023, w, "P%02d" % i, "missed", side="over"))
    for w in range(1, 4):                          # 2024: 3 cleared in all
        rows.append(_prop(2024, w, "Q", "cleared"))
        rows.append(_prop(2024, w, "R", "missed"))
        rows.append(_prop(2024, w, "Q", "missed", side="over"))
    rows.append(_spread(2023, 1, "TTT", 4.5, "missed"))
    out = L.build(_universe(rows), GEN, log=lambda *_: None)
    f = out["lab/nfl/presets/fade_every_over.json"]
    assert f["returns"]["steps"] == []
    assert "season 2024 has 3 cleared" in f["returns"]["withheld"]
    assert f["luck"]["paths"] is None
    E.validate_contract({"lab/nfl/presets/fade_every_over.json": f})


def test_verify_series_refuses_a_thin_step_a_split_week_and_a_wrong_point():
    """The runtime guard is shown refusing, on each of its failure shapes."""
    r = _engine("fade_every_over")
    assert "steps over" in L.verify_series(r).statement
    with pytest.raises(TypeError):
        bool(L.verify_series(r))
    # 1. a thin step: step 0 keeps only its last week, the rest moves to step 1
    bad = copy.deepcopy(r)
    s0, s1 = bad["series"]["steps"][0], bad["series"]["steps"][1]
    last = max(bad["bet_list"][i]["week"] for i in s0["members"])
    keep = [i for i in s0["members"] if bad["bet_list"][i]["week"] == last]
    moved = [i for i in s0["members"] if i not in keep]
    assert sum(bad["bet_list"][i]["outcome"] == "cleared" for i in keep) < L.MIN_CLEARED
    s0["members"], s1["members"] = keep, sorted(moved + s1["members"])
    with pytest.raises(SystemExit, match="fewer than 10"):
        L.verify_series(bad)
    # 2. a week block split across two steps
    bad = copy.deepcopy(r)
    s0, s1 = bad["series"]["steps"][0], bad["series"]["steps"][1]
    s1["members"] = sorted(s1["members"] + [s0["members"].pop()])
    with pytest.raises(SystemExit, match="split"):
        L.verify_series(bad)
    # 3. a point that does not re-derive from the bet list
    bad = copy.deepcopy(r)
    bad["series"]["steps"][0]["units"] += 0.5
    with pytest.raises(SystemExit, match="units"):
        L.verify_series(bad)


@pytest.fixture(scope="module")
def big():
    """Enough bets that chase_the_streak clears the floor in every season and
    draws from a pool larger than itself, so it has a band to publish."""
    return L.build(_universe(_rows(np.random.default_rng(11), per_week=30)), GEN,
                   log=lambda *_: None)


def test_the_band_is_published_at_the_steps_only_and_matches_them(big):
    seen = 0
    for f in big.values():
        if f.get("kind") != "lab_preset" or f["luck"]["paths"] is None:
            continue
        p, steps = f["luck"]["paths"], f["returns"]["steps"]
        assert p["index"] == [s["index"] for s in steps]
        assert p["p5"] == [s["null_p5"] for s in steps]
        assert p["p95"] == [s["null_p95"] for s in steps]
        assert all(s["cleared"] >= L.MIN_CLEARED for s in steps)
        assert f["returns"]["band_note"] is None
        seen += 1
    assert seen > 0


def test_the_bet_level_band_the_file_used_to_carry_leaks_a_price():
    """Discriminates the test above: at bet granularity the band's first p95 is
    ONE random bet's profit - with every fixture price at -110, exactly 10/11."""
    band = _engine("chase_the_streak")["luck"]["band"]
    assert band["index"][0] == 0
    assert band["p95"][0] == pytest.approx(100 / 110, abs=1e-3)


def test_asking_for_the_series_moves_nothing_else_in_the_result():
    from lab import run
    u = _universe(_rows(np.random.default_rng(11), per_week=30))
    s = next(p for p in L.PRESETS if p["key"] == "chase_the_streak")["strategy"]
    a, b = run(s, u), run(s, u, series_min_cleared=L.MIN_CLEARED)
    assert a["series"] is None and b["series"] is not None
    assert a["luck"]["band_at"] is None and b["luck"]["band_at"] is not None
    la, lb = dict(a["luck"]), dict(b["luck"])
    la.pop("band_at"), lb.pop("band_at")
    assert la == lb
    for k in ("summary", "by_season", "segments", "drawdown", "bet_list", "verdict"):
        assert a[k] == b[k], k


def test_the_contract_refuses_a_bet_level_field_on_a_step_or_the_drawdown(files):
    key = "lab/nfl/presets/fade_every_over.json"
    bad = copy.deepcopy(files[key])
    bad["returns"]["steps"][0]["members"] = [0, 1, 2]
    with pytest.raises(E.ContractError, match="members"):
        E.validate_contract({key: bad})
    bad = copy.deepcopy(files[key])
    bad["drawdown"]["peak_index"] = 3
    with pytest.raises(E.ContractError, match="peak_index"):
        E.validate_contract({key: bad})


# ------------------------------------------------------------------ the drawdown (a-44)

class _Members:
    def __init__(self, members):
        self.members = members


def _dd_result(outcomes, peak, trough):
    """A result carrying only what verify_drawdown reads. Every cleared bet pays 1.0."""
    bl = [{"season": 2023, "week": i + 1, "market": "receptions", "outcome": o,
           "profit": 1.0 if o == "cleared" else -1.0} for i, o in enumerate(outcomes)]
    cum = np.concatenate([[0.0], np.cumsum([b["profit"] for b in bl])])
    p = -1 if peak is None else peak
    return {"drawdown": {"max_units": float(cum[p + 1] - cum[trough + 1]),
                         "peak_index": peak, "trough_index": trough},
            "bet_list": bl, "strategy": {"staking": {"method": "flat", "unit": 1}},
            "summary": {"cleared": sum(o == "cleared" for o in outcomes)},
            "by_season": [], "segments": {}}


def test_a_drawdown_over_misses_only_is_published_it_reads_back_to_no_price():
    outs = ["cleared"] * 12 + ["missed"] * 5 + ["cleared"] * 12
    r = _dd_result(outs, peak=11, trough=16)
    ok, why = L.verify_drawdown(r, _Members([set(range(0, 12)), set(range(12, 29))]))
    assert ok, why


def test_a_drawdown_whose_window_holds_a_few_prices_is_withheld():
    outs = (["cleared"] * 12
            + ["missed", "cleared", "missed", "cleared", "missed", "cleared", "missed", "missed"]
            + ["cleared"] * 12)
    r = _dd_result(outs, peak=11, trough=19)
    ok, why = L.verify_drawdown(r, _Members([set(range(0, 12)), set(range(12, 32))]))
    assert not ok and "the window itself holds 3 cleared" in why


def test_a_drawdown_is_withheld_when_the_steps_isolate_a_thin_fragment():
    """The window holds 14 cleared - fine alone - but a published step lies wholly
    inside it, and the window less that step leaves 2 cleared bets."""
    outs = (["cleared"] * 10 + ["missed"] * 3 + ["cleared", "missed"] * 12
            + ["missed", "cleared", "missed", "cleared", "missed"] + ["cleared"] * 10)
    r = _dd_result(outs, peak=9, trough=41)
    D = set(range(10, 42))
    atoms = [set(range(0, 10)), set(range(10, 37)), set(range(37, len(outs)))]
    assert sum(outs[i] == "cleared" for i in D) >= L.MIN_CLEARED
    ok, why = L.verify_drawdown(r, _Members(atoms))
    assert not ok and "less the steps inside it holds 2 cleared" in why
    # with no step boundary inside the window, the verdict turns on G alone - the
    # one step it touches, less the window - so it is decided by that count
    ok, why = L.verify_drawdown(r, _Members([set(range(0, 10)), set(range(10, len(outs)))]))
    G = set(range(10, len(outs))) - D
    assert (sum(outs[i] == "cleared" for i in G) >= L.MIN_CLEARED) == ok


def test_the_published_drawdowns_pass_their_own_check(files):
    seen = 0
    for f in files.values():
        if f.get("kind") != "lab_preset":
            continue
        d = f["drawdown"]
        if d["max_units"] is not None:
            assert d["check"] and d["withheld"] is None
            seen += 1
        else:
            assert d["withheld"] and d["check"] is None
    assert seen > 0
