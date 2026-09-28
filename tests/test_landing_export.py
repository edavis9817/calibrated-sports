"""`landing.json` (a-47): one file, every figure read from a file and re-derived.

Run: pytest -q tests/test_landing_export.py

Every guard here is shown firing as well as passing: a check that has only ever
been seen to pass cannot be told apart from one that checks nothing.
"""
import copy
import json
import os

import pytest

from jobs import export_web as E
from jobs import landing_export as L
from jobs import metric_registry as MR

PKEY = "2026-3"


def _write(root, key, obj):
    p = os.path.join(root, *key.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f)


def _rungs(n, top=0.9, ts=1790000000.0):
    return [{"line": 0.5 + i, "p_over": round(top - i * 0.08, 3), "bid": round(top - i * 0.08 - 0.005, 3),
             "ask": round(top - i * 0.08 + 0.005, 3), "quote_ts": ts} for i in range(n)]


def _cdf():
    return [{"x": x, "p_at_most": round(min(1.0, x / 30.0), 4)} for x in range(0, 31)]


def _dist(q50):
    return {"cdf": _cdf(), "quantiles": {"q10": 2.0, "q25": 5.0, "q50": q50, "q75": 14.0, "q90": 20.0},
            "thresholds": [{"points": 10, "p_at_least": 0.6}, {"points": 20, "p_at_least": 0.25}]}


def _market(pid, name, team, opp, rec, rush=0, as_of="2026-09-28T21:00:00Z"):
    comps = [{"stat": "rec", "basis": "MARKET", "rungs": _rungs(rec)},
             {"stat": "rec_yds", "basis": "DERIVED", "note": "x"},
             {"stat": "rush_att", "basis": "MARKET", "rungs": _rungs(rush)}]
    return {"schema_version": 2, "generated_at": "2026-09-28T21:07:53Z", "kind": "market",
            "sport": "nfl", "identity": {"id": pid, "slug": name.lower().replace(" ", "-"),
                                         "name": name, "position": "WR", "team": team},
            "period": {"index": 3, "label": "Week 3", "key": PKEY, "season": 2026},
            "game_id": "2026_03_PHI_CHI", "opponent": opp, "kickoff_ts": 1790640900.0,
            "as_of": as_of, "source": {"venue": "kalshi", "method": "m", "n_sims": 10},
            "components": comps,
            "distributions": {"ppr": _dist(9.0), "half": _dist(7.5), "standard": _dist(6.0)}}


TEAMS = [{"slug": "phi", "abbr": "PHI", "name": "Philadelphia Eagles"},
         {"slug": "chi", "abbr": "CHI", "name": "Chicago Bears"}]
DEN = {"participation": {"noun": "offensive usage", "definition": "d"},
       "archive": {"divisible": False, "span": {"season_from": 1999, "season_to": 2026, "seasons": 28},
                   "players": 3993, "games": {"final": 7323, "season_from": 1999, "season_to": 2026}},
       "season": {"divisible": False, "span": {"season": 2026}, "players": 700,
                  "games": {"final": 46, "scheduled": 272}},
       "week": {"divisible": True, "span": {"season": 2026, "period": {"index": 3, "label": "Week 3",
                                                                     "key": PKEY}},
                "games": {"scheduled": 16, "final": 15, "open": 1, "awaiting_final": 0},
                "players": {"played": 400, "expected": 40, "priced": 3, "priced_expected": 3},
                "share_priced": 0.075}}


def manifest(denominators=True):
    m = {"schema_version": 2, "generated_at": "2026-09-28T21:07:53Z", "kind": "sport_manifest",
         "sport": "nfl",
         "current": {"season": 2026, "period": {"index": 3, "label": "Week 3", "key": PKEY},
                     "data_through": {"season": 2026, "index": 3}, "source_version": "2026-09-28",
                     "stale": False, "stale_reason": None},
         "seasons": list(range(1999, 2027)),
         "market_definitions": {"receptions": {"label": "Receptions", "stat": "rec"},
                                "rush_attempts": {"label": "Rush Attempts", "stat": "rush_att"},
                                "sacks": {"label": "Sacks", "stat": None}},
         "scoring_presets": {k: {"label": v, "weights": {"rec": w}, "bonuses": []}
                             for k, v, w in (("ppr", "PPR", 1), ("half", "Half PPR", 0.5),
                                             ("standard", "Standard", 0))},
         "teams": TEAMS, "counts": {"players": 3993, "teams": 32, "market": 3, "games": 7323,
                                    "rungs": 0}}
    if denominators:
        m["denominators"] = copy.deepcopy(DEN)
    return m


HYP = [
    {"id": "R01", "question": "q", "verdict": "retired", "metric": "realized rate (priced 0.1388)",
     "estimate": 0.0898, "interval": [0.0552, 0.1429], "unit": "probability", "n": 167, "games": None},
    {"id": "R02", "question": "q", "verdict": "retired", "metric": "clv", "estimate": -13.52,
     "interval": [-17.06, -10.4], "unit": "pp", "n": 493, "games": 14},
    {"id": "R06", "question": "q", "verdict": "retired", "metric": "episodes", "estimate": 232,
     "interval": None, "unit": "episodes of 892", "n": 892, "games": None},
    {"id": "R10", "question": "q", "verdict": "retired", "metric": "Brier(model) - Brier(market)",
     "estimate": 0.024, "interval": [-0.001, 0.0373], "unit": "Brier", "n": 706, "games": 14},
]


def board_read(team="PHI", over=-106.0, under=-120.0, p_over_devig=0.4854, hold=0.06):
    row = {"row_id": "2026-03-PHI-CHI:00-0000002:receptions:5.5", "gsis_id": "00-0000002",
           "slug": "b-two", "name": "B Two", "pos": "WR", "team": team, "market": "receptions",
           "line": 5.5, "is_main": True, "kalshi_mid": 0.485,
           "books": [{"book": "draftkings", "over": over, "under": under,
                      "read_at": "2026-09-26T00:14:54Z", "p_over_devig": p_over_devig,
                      "hold": hold}]}
    other = {**copy.deepcopy(row), "row_id": "2026-03-PHI-CHI:00-0000009:receptions:3.5",
             "gsis_id": "00-0000009", "line": 3.5}
    return {"kind": "board_read", "read_at": "2026-09-27T06:02:33Z", "season": 2026, "week": 3,
            "rows": [row, other]}


@pytest.fixture
def tree(tmp_path):
    """A web tree, a Board tree and a universe meta. Three market files:
    00-0000002 has 10 receptions rungs (the featured ladder), 00-0000001 has
    6 + 3 = 9 rungs across two ladders, 00-0000003 has 4."""
    web, board = str(tmp_path / "web"), str(tmp_path / "board")
    _write(web, "nfl/manifest.json", manifest())
    for m in (_market("00-0000001", "A One", "chi", "phi", 6, 3),
              _market("00-0000002", "B Two", "phi", "chi", 10),
              _market("00-0000003", "C Three", "chi", "phi", 4)):
        _write(web, f"nfl/market/{m['identity']['id']}/{PKEY}.json", m)
    _write(web, "nfl/market/00-0000002/2026-2.json", _market("00-0000002", "B Two", "phi", "chi", 12))
    _write(web, "research/hypotheses.json", {"generated_at": "2026-09-24T22:59:55Z",
                                             "hypotheses": HYP})
    _write(web, "research/calibration.json", {
        "generated_at": "2026-09-26T18:28:46Z", "population": "p", "n": 935, "games": 14,
        "series": [{"name": "market", "ece": 0.05, "bins": [
            {"lo": 0.0, "hi": 0.1, "n": 5, "mean_forecast": 0.05, "realized": 0.1,
             "wilson": [0.01, 0.3]}]}]})
    _write(web, "research/market_calibration.json", {"population": {"n": 44198, "games": 814,
                                                                    "seasons": [2023, 2024, 2025]}})
    _write(web, "lab/nfl/index.json", {"universe_built": "2026-09-27T06:14:56Z"})
    _write(board, "board/nfl/2026/wk03/index.json",
           {"latest": "2026-09-27T06:02:33Z", "leans": {"graded": 0, "upcoming": 190, "live": 0,
                                                        "void": 2}})
    _write(board, "board/nfl/2026/wk03/read-2026-09-27T060233Z.json", board_read())
    meta = str(tmp_path / "universe.meta.json")
    with open(meta, "w") as f:
        json.dump({"built": "2026-09-27T06:14:56+00:00", "rows": 552646,
                   "price_coverage": {"prop": [2023, 2025], "spread": [1999, 2026]}}, f)
    return web, board, meta


def built(tree, **kw):
    web, board, meta = tree
    files, missing = L.load_inputs(web, kw.get("board", board), kw.get("meta", meta))
    return L.build(files, missing, generated_at="2026-09-28T22:00:00Z"), files


def counters(p):
    return {c["id"]: c for c in p["counters"]}


# ------------------------------------------------------------------ the whole file

def test_the_file_validates_and_is_written_through_the_gate(tree, tmp_path):
    p, files = built(tree)
    out = str(tmp_path / "out")
    written, deleted, statement = L.publish(p, files, out)
    assert (written, deleted) == (1, 0)
    assert "0 disagreements" in statement
    on_disk = json.load(open(os.path.join(out, "landing.json"), encoding="utf-8"))
    assert on_disk["kind"] == "landing" and on_disk["sport"] is None
    assert E.kind_for_key("landing.json")[0] == "landing"


def test_it_owns_no_prefix_so_it_deletes_nothing(tree, tmp_path):
    p, files = built(tree)
    out = str(tmp_path / "out")
    _write(out, "sports.json", {"kept": True})
    _write(out, "nfl/manifest.json", {"kept": True})
    L.publish(p, files, out)
    assert os.path.isfile(os.path.join(out, "sports.json"))
    assert os.path.isfile(os.path.join(out, "nfl", "manifest.json"))


def test_only_the_current_period_market_files_are_read(tree):
    p, files = built(tree)
    assert "nfl/market/00-0000002/2026-2.json" not in files
    assert p["featured_ladder"]["period"]["key"] == PKEY
    assert len(p["featured_ladder"]["rungs"]) == 10        # not the 12 of week 2


# ------------------------------------------------------------------ 1. counters and tiers

def test_every_counter_carries_a_tier_and_the_hero_count_is_the_week(tree):
    p, _ = built(tree)
    c = counters(p)
    assert c["markets"]["tier"] == "week" and c["markets"]["value"] == 3
    assert c["markets"]["span"]["period"]["key"] == PKEY
    assert c["players"]["tier"] == "archive" and c["players"]["value"] == 3993
    assert c["players_season"]["tier"] == "season" and c["players_season"]["value"] == 700
    assert c["seasons"]["value"] == 28                      # 1999-2026, counted
    assert c["leans"]["value"] == 192 and c["lines_posted"]["value"] == 2
    assert c["universe_rows"]["value"] == 552646 and c["universe_rows"]["source"]["served"] is False
    assert c["tests"]["value"] == len(HYP)
    assert {x["tier"] for x in p["counters"]} <= set(L.TIERS)
    assert all(x["sport"] == "nfl" for x in p["counters"])


def test_a_manifest_before_a46_falls_back_to_the_registered_copies(tree):
    web, board, meta = tree
    _write(web, "nfl/manifest.json", manifest(denominators=False))
    p, files = built(tree)
    c = counters(p)
    assert c["players"]["source"]["path"] == "counts.players"
    assert c["markets"]["source"]["path"] == "counts.market"
    assert c["players_season"]["value"] is None and c["players_season"]["reason"]
    assert L.verify(p, files).clean


@pytest.mark.parametrize("den", [True, False])
def test_a_named_metric_is_one_of_its_registered_locations(tree, den):
    web, _, _ = tree
    _write(web, "nfl/manifest.json", manifest(denominators=den))
    p, _ = built(tree)
    reg = {m["id"]: m for m in MR.METRICS}
    named = [c for c in p["counters"] if c["metric"]]
    assert named
    for c in named:
        m = reg[c["metric"]]
        locs = [(m["source"]["file"], m["source"]["path"])] + [(x["file"], x["path"])
                                                               for x in m["copies"]]
        assert (c["source"]["key"], c["source"]["path"]) in locs, c["id"]


def test_verify_refuses_a_counter_that_does_not_re_derive(tree, tmp_path):
    p, files = built(tree)
    assert L.verify(p, files).clean
    c = counters(p)["markets"]
    c["value"] += 1
    rep = L.verify(p, files)
    assert not rep.clean and "markets" in rep.statement
    with pytest.raises(L.LandingError):
        L.publish(p, files, str(tmp_path / "out"))
    assert not os.path.exists(os.path.join(str(tmp_path / "out"), "landing.json"))


def test_verify_refuses_a_metric_id_that_does_not_own_the_path(tree):
    p, files = built(tree)
    counters(p)["rungs"]["metric"] = "coverage.players.archive"
    assert not L.verify(p, files).clean


def test_verify_refuses_a_tampered_scene(tree):
    p, files = built(tree)
    p["featured_ladder"]["rungs"][0]["mid"] = 0.5
    assert not L.verify(p, files).clean
    p, files = built(tree)
    p["register"]["rows"][0]["estimate"] = 0.2
    assert not L.verify(p, files).clean


def test_the_report_refuses_truth_testing(tree):
    p, files = built(tree)
    with pytest.raises(TypeError):
        bool(L.verify(p, files))


def test_the_universe_count_is_withheld_when_it_is_not_the_served_universe(tree, tmp_path):
    meta = str(tmp_path / "other.meta.json")
    with open(meta, "w") as f:
        json.dump({"built": "2026-09-30T00:00:00+00:00", "rows": 1, "price_coverage": {}}, f)
    p, _ = built(tree, meta=meta)
    c = counters(p)["universe_rows"]
    assert c["value"] is None and "not the universe" in c["reason"]
    assert any(u["part"] == "counters.universe_rows" for u in p["unavailable"])


# ------------------------------------------------------------------ 2 / 4 / 7. selection rules

def test_the_featured_ladder_is_the_most_rungs(tree):
    p, _ = built(tree)
    fl = p["featured_ladder"]
    assert fl["player"]["id"] == "00-0000002" and fl["market"]["key"] == "receptions"
    assert fl["opponent"]["abbr"] == "CHI" and fl["candidates"] == 4
    assert fl["rule"] == L.RULE_FEATURED


def test_a_tie_goes_to_the_most_recent_read(tree):
    web, _, _ = tree
    _write(web, f"nfl/market/00-0000004/{PKEY}.json",
           _market("00-0000004", "D Four", "chi", "phi", 10, as_of="2026-09-28T21:30:00Z"))
    p, _ = built(tree)
    assert p["featured_ladder"]["player"]["id"] == "00-0000004"
    _write(web, f"nfl/market/00-0000004/{PKEY}.json",
           _market("00-0000004", "D Four", "chi", "phi", 10, as_of="2026-09-28T20:00:00Z"))
    p, _ = built(tree)
    assert p["featured_ladder"]["player"]["id"] == "00-0000002"


def test_distributions_rank_on_total_rungs_and_say_when_short(tree):
    p, _ = built(tree)
    ds = p["distributions"]
    assert [x["player"]["id"] for x in ds["players"]] == ["00-0000002", "00-0000001", "00-0000003"]
    assert [x["rungs"] for x in ds["players"]] == [10, 9, 4]
    assert ds["published"] == 3 and ds["requested"] == L.N_DISTRIBUTIONS
    assert any(u["part"] == "distributions" and "3 of 14" in u["reason"]
               for u in p["unavailable"])
    s = ds["players"][0]["survival"]
    assert s[0] == {"x": 0, "p_over": 1.0} and s[-1]["p_over"] == 0.0


def test_the_fantasy_example_is_the_featured_player_under_three_presets(tree):
    p, _ = built(tree)
    fa = p["fantasy"]
    assert fa["player"]["id"] == p["featured_ladder"]["player"]["id"]
    assert [x["preset"] for x in fa["presets"]] == ["ppr", "half", "standard"]
    assert [x["median"] for x in fa["presets"]] == [9.0, 7.5, 6.0]
    assert all(x["p_at_least"] == {"points": 20, "p": 0.25} for x in fa["presets"])


# ------------------------------------------------------------------ 3. de-vig

def test_the_devig_is_the_featured_claim_with_both_sides_and_the_overround(tree):
    p, _ = built(tree)
    d = p["devig"]
    assert d["basis"] == "book" and d["player"]["id"] == "00-0000002"
    assert (d["over_american"], d["under_american"]) == (-106.0, -120.0)
    assert d["implied_over"] == round(106 / 206, 4) and d["implied_under"] == round(120 / 220, 4)
    assert d["overround"] == round(106 / 206 + 120 / 220, 4)
    assert abs(d["devig_over"] + d["devig_under"] - 1.0) < 1e-9
    assert d["exchange"]["mid"] == 0.485


def test_the_devig_refuses_when_it_disagrees_with_the_board(tree):
    _, board, _ = tree
    _write(board, "board/nfl/2026/wk03/read-2026-09-27T060233Z.json",
           board_read(p_over_devig=0.40))
    with pytest.raises(L.LandingError, match="disagrees"):
        built(tree)


def test_without_a_board_the_devig_is_the_exchange_version_labelled(tree, tmp_path):
    p, _ = built(tree, board=None)
    d = p["devig"]
    assert d["basis"] == "exchange" and d["hold"] == 0.0 and d["over_american"] is None
    assert "no margin" in d["note"]
    assert any(u["part"] == "devig" for u in p["unavailable"])
    assert counters(p)["lines_posted"]["value"] is None


# ------------------------------------------------------------------ 6. the register

def test_each_row_carries_its_own_null():
    assert L.null_of({"id": "x", "unit": "pp"})[0] == 0.0
    assert L.null_of({"id": "x", "unit": "Brier"})[0] == 0.0
    assert L.null_of({"id": "x", "unit": "probability", "metric": "r (priced 0.1388)"})[0] == 0.1388
    assert L.null_of({"id": "x", "unit": "episodes of 892"})[0] is None
    assert L.null_of({"id": "x", "unit": None})[0] is None
    with pytest.raises(L.LandingError):
        L.null_of({"id": "x", "unit": "probability", "metric": "no price stated"})


def test_excludes_can_answer_every_way():
    assert L.excludes([0.1, 0.2], 0.0) is True
    assert L.excludes([-0.2, -0.1], 0.0) is True
    assert L.excludes([-0.1, 0.1], 0.0) is False
    assert L.excludes(None, 0.0) is None
    assert L.excludes([0.1, 0.2], None) is None


def test_the_register_rows_against_their_own_nulls(tree):
    p, _ = built(tree)
    rows = {r["id"]: r for r in p["register"]["rows"]}
    assert rows["R01"]["null"] == 0.1388 and rows["R01"]["excludes_null"] is False
    assert rows["R02"]["excludes_null"] is True
    assert rows["R06"]["null"] is None and rows["R06"]["excludes_null"] is None
    assert rows["R10"]["excludes_null"] is False
    assert p["register"]["counts"] == {"rows": 4, "excludes_null": 1, "includes_null": 2,
                                       "no_interval_or_null": 1}


# ------------------------------------------------------------------ 8. freshness

def test_freshness_names_the_newest_read_and_its_source(tree):
    p, _ = built(tree)
    f = p["freshness"]
    assert f["last_read"] == "2026-09-28T21:00:00Z" and f["source"] == "kalshi.ladders"
    assert {r["source"] for r in f["reads"]} == {"kalshi.ladders", "oddsapi"}
