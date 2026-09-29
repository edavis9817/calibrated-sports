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


def test_the_current_period_wins_while_it_has_a_ladder(tree):
    # a-52: an earlier period inside the window IS read now (it is what a part
    # falls back to) - but only when the current period cannot satisfy the rule.
    # Before a-52 this asserted the week-2 file was never read at all.
    p, files = built(tree)
    assert "nfl/market/00-0000002/2026-2.json" in files
    assert p["featured_ladder"]["period"]["key"] == PKEY
    assert len(p["featured_ladder"]["rungs"]) == 10        # not the 12 of week 2
    assert p["featured_ladder"]["provenance"]["carried"] is False
    assert p["carried"] == []


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


# ------------------------------------------------------------------ a-52: the landing never goes blank
#
# The showpieces fall back, each on its own, to the most recent period in the
# window whose files satisfy the part's UNCHANGED rule, and carry provenance and
# - from a played period - the result. Selection never reads the result.

from core import board as B  # noqa: E402

GAME = {1: "2026_01_PHI_CHI", 2: "2026_02_PHI_CHI", 3: "2026_03_PHI_CHI"}


def _at(pid, name, rec, index, as_of="2026-09-20T21:00:00Z", ppr=True):
    m = _market(pid, name, "phi", "chi", rec, as_of=as_of)
    m["period"] = {"index": index, "label": f"Week {index}", "key": f"2026-{index}",
                   "season": 2026}
    m["game_id"] = GAME[index]
    if not ppr:
        m["distributions"] = {}
    return m


def _empty_current(web):
    """The Monday tree: nothing served for the current period."""
    for pid in ("00-0000001", "00-0000002", "00-0000003"):
        os.remove(os.path.join(web, "nfl", "market", pid, f"{PKEY}.json"))
    os.remove(os.path.join(web, "nfl", "market", "00-0000002", "2026-2.json"))


def _season(web, pid, rows):
    """The player's season file: {index: stats}."""
    _write(web, f"nfl/players/{pid}/2026.json", {
        "kind": "player_season", "identity": {"id": pid}, "season": 2026,
        "periods": [{"season": 2026, "index": i, "label": f"Week {i}", "game_id": GAME[i],
                     "stats": st} for i, st in rows.items()]})


def _ledger(board, events):
    """events: [(event, lean_id, gsis, index, line, side, result, actual, read_at)]."""
    import csv as _csv
    p = os.path.join(board, "board", "nfl", "ledger.csv")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="") as f:
        w = _csv.DictWriter(f, fieldnames=B.LEDGER_COLUMNS)
        w.writeheader()
        for ev, lid, gsis, idx, line, side, res, act, read_at in events:
            row = {c: "" for c in B.LEDGER_COLUMNS}
            row.update(event=ev, lean_id=lid, gsis_id=gsis, season="2026", week=str(idx),
                       market="receptions", line=str(line), side=side, result=res or "",
                       actual="" if act is None else str(act), read_at=read_at)
            w.writerow(row)


def fb(tree, archive=None, n=3):
    web, board, meta = tree
    files, missing = L.load_inputs(web, board, meta, archive, n)
    return L.build(files, missing, generated_at="2026-09-28T22:00:00Z"), files


def _parts(p):
    return {u["part"] for u in p["unavailable"]}


def test_current_empty_previous_full_carries_every_part_with_provenance(tree, tmp_path):
    web, _, _ = tree
    _empty_current(web)
    _write(web, "nfl/market/00-0000005/2026-2.json", _at("00-0000005", "E Five", 9, 2))
    p, files = fb(tree)
    for part in ("featured_ladder", "distributions", "fantasy"):
        prov = p[part]["provenance"]
        assert prov == {"carried": True, "walked": 1, "as_of": "2026-09-20T21:00:00Z",
                        "period": {"index": 2, "label": "Week 2", "key": "2026-2",
                                   "season": 2026}, "reason": prov["reason"]}, part
        assert "Week 3" in prov["reason"] and "Week 2" in prov["reason"]
    # The file's own period stays the current one; the part says where it is from.
    assert p["period"]["key"] == PKEY
    assert [c["part"] for c in p["carried"]] == ["featured_ladder", "distributions", "fantasy"]
    assert all(c["period"]["key"] == "2026-2" and c["walked"] == 1 for c in p["carried"])
    # Carried is not unavailable. (Distributions is listed only as SHORT, the
    # pre-a-52 note for fewer than 14, naming the period it counted in.)
    assert not {"featured_ladder", "fantasy"} & _parts(p)
    assert [u["reason"] for u in p["unavailable"] if u["part"] == "distributions"] == \
        ["1 of 14 requested: only 1 players carry a posted market in Week 2"]
    assert p["featured_ladder"]["candidates"] == 1
    # A carried ladder is not a current price, so the exchange de-vig does not use it.
    assert p["devig"]["basis"] == "book"
    L.publish(p, files, str(tmp_path / "out"))           # validates against the contract


def test_a_carried_ladder_is_never_the_exchange_devig(tree):
    web, _, _ = tree
    _empty_current(web)
    _write(web, "nfl/market/00-0000005/2026-2.json", _at("00-0000005", "E Five", 9, 2))
    p, _ = fb((web, None, tree[2]))                        # no Board: no book price either
    assert p["featured_ladder"]["provenance"]["carried"] is True
    assert p["devig"] is None and "devig" in _parts(p)


def test_current_and_previous_empty_two_back_full(tree):
    web, _, _ = tree
    _empty_current(web)
    _write(web, "nfl/market/00-0000005/2026-1.json", _at("00-0000005", "E Five", 9, 1))
    p, _ = fb(tree)
    assert p["featured_ladder"]["provenance"]["walked"] == 2
    assert p["featured_ladder"]["provenance"]["period"]["key"] == "2026-1"
    assert p["distributions"]["provenance"]["walked"] == 2


def test_nothing_in_the_window_has_a_ladder_so_the_part_is_null(tree):
    web, _, _ = tree
    _empty_current(web)
    # Week 1 is 2 back; a bound of 1 must not reach it.
    _write(web, "nfl/market/00-0000005/2026-1.json", _at("00-0000005", "E Five", 9, 1))
    p, _ = fb(tree, n=1)
    assert p["featured_ladder"] is None and p["distributions"] is None and p["fantasy"] is None
    assert {"featured_ladder", "distributions", "fantasy"} <= _parts(p)
    assert p["carried"] == []
    why = next(u["reason"] for u in p["unavailable"] if u["part"] == "featured_ladder")
    assert "the 1 before it" in why
    # And the same tree with the default bound reaches it: the bound is what refused.
    p, _ = fb(tree, n=3)
    assert p["featured_ladder"]["provenance"]["walked"] == 2


def test_the_walk_stops_at_the_first_period_of_the_season():
    m = manifest()
    m["current"]["period"] = {"index": 2, "label": "Week 2", "key": "2026-2"}
    assert [w[3] for w in L.window(m, 3)] == ["2026-2", "2026-1"]
    assert [w[3] for w in L.window(m, 0)] == ["2026-2"]


def test_distributions_fall_back_while_the_featured_ladder_does_not(tree):
    web, _, _ = tree
    _empty_current(web)
    # The current period has a ladder with no fantasy distribution...
    _write(web, f"nfl/market/00-0000006/{PKEY}.json",
           _at("00-0000006", "F Six", 5, 3, ppr=False))
    # ...and the previous one has distributions.
    _write(web, "nfl/market/00-0000005/2026-2.json", _at("00-0000005", "E Five", 9, 2))
    p, _ = fb(tree)
    assert p["featured_ladder"]["provenance"]["carried"] is False
    assert p["featured_ladder"]["player"]["id"] == "00-0000006"
    assert p["distributions"]["provenance"]["carried"] is True
    assert p["distributions"]["provenance"]["period"]["key"] == "2026-2"
    assert p["fantasy"]["provenance"]["carried"] is True
    assert [c["part"] for c in p["carried"]] == ["distributions", "fantasy"]


def _two_candidates(tree, a_rec, b_rec, a_lean_result, b_lean_result):
    """Week 2: A has 10 rungs (the rule's pick), B has 8. Settled stats and
    graded leans as given."""
    web, board, _ = tree
    _empty_current(web)
    _write(web, "nfl/market/00-0000007/2026-2.json", _at("00-0000007", "A Pick", 10, 2))
    _write(web, "nfl/market/00-0000008/2026-2.json", _at("00-0000008", "B Other", 8, 2))
    _season(web, "00-0000007", {2: {"rec": a_rec, "rec_yds": 10 * a_rec}})
    _season(web, "00-0000008", {2: {"rec": b_rec, "rec_yds": 10 * b_rec}})
    _ledger(board, [
        ("published", "leanA", "00-0000007", 2, 5.5, "over", None, None, "2026-09-18T00:00:00Z"),
        ("published", "leanB", "00-0000008", 2, 5.5, "over", None, None, "2026-09-18T00:00:00Z"),
        ("graded", "leanA", "00-0000007", 2, 5.5, "over", a_lean_result, a_rec,
         "2026-09-18T00:00:00Z"),
        ("graded", "leanB", "00-0000008", 2, 5.5, "over", b_lean_result, b_rec,
         "2026-09-18T00:00:00Z"),
    ])


def test_selection_does_not_depend_on_the_outcome_the_loser_is_published(tree, tmp_path):
    # THE GUARDRAIL. The rule picks A (most rungs). A lost, B won. A is published,
    # with its miss on it.
    _two_candidates(tree, a_rec=2, b_rec=9, a_lean_result="missed", b_lean_result="cleared")
    p, files = fb(tree)
    fl = p["featured_ladder"]
    assert fl["player"]["id"] == "00-0000007"
    assert fl["lean"]["result"] == "missed" and fl["lean"]["status"] == "graded"
    assert fl["result"]["value"] == 2
    assert [r["outcome"] for r in fl["result"]["rungs"]][:3] == ["cleared", "cleared", "missed"]
    assert p["fantasy"]["player"]["id"] == "00-0000007"
    assert L.verify(p, files).clean
    L.publish(p, files, str(tmp_path / "out"))


def test_selection_does_not_depend_on_the_outcome_swapped(tree):
    # The discriminating half: flip who won and the pick does not move.
    _two_candidates(tree, a_rec=9, b_rec=2, a_lean_result="cleared", b_lean_result="missed")
    p, _ = fb(tree)
    assert p["featured_ladder"]["player"]["id"] == "00-0000007"
    assert p["featured_ladder"]["lean"]["result"] == "cleared"


def test_the_carried_part_is_shown_with_its_settled_result(tree):
    _two_candidates(tree, a_rec=2, b_rec=9, a_lean_result="missed", b_lean_result="cleared")
    p, _ = fb(tree)
    r = p["featured_ladder"]["result"]
    assert r["source"] == {"key": "nfl/players/00-0000007/2026.json",
                           "path": "periods[game_id=2026_02_PHI_CHI].stats.rec"}
    assert r["stat"] == "rec" and r["game_id"] == GAME[2]
    ln = p["featured_ladder"]["lean"]
    assert (ln["side"], ln["line"], ln["actual"]) == ("over", 5.5, 2.0)
    assert ln["source"] == {"key": "board/nfl/ledger.csv", "path": "[lean_id=leanA]"}
    # Fantasy: the manifest fixture's presets weight rec only, 1 / 0.5 / 0.
    fa = p["fantasy"]
    assert [x["result"]["points"] for x in fa["presets"]] == [2.0, 1.0, 0.0]
    assert fa["presets"][0]["result"]["band"] == "q10-q25"         # q10 = 2.0, inclusive
    assert fa["presets"][2]["result"]["band"] == "below q10"
    ds = {x["player"]["id"]: x for x in p["distributions"]["players"]}
    assert ds["00-0000008"]["result"]["points"] == 9.0
    assert ds["00-0000008"]["result"]["band"] == "q50-q75"
    assert all(c["settled"] for c in p["carried"])


def test_a_period_that_is_not_settled_claims_no_result(tree):
    web, _, _ = tree
    _empty_current(web)
    _write(web, "nfl/market/00-0000005/2026-2.json", _at("00-0000005", "E Five", 9, 2))
    _season(web, "00-0000005", {1: {"rec": 4, "rec_yds": 40}})     # no week-2 row
    p, _ = fb(tree)
    assert p["featured_ladder"]["result"] is None and p["featured_ladder"]["lean"] is None
    assert p["fantasy"]["result"] is None
    assert all(x["result"] is None for x in p["fantasy"]["presets"])
    assert p["distributions"]["players"][0]["result"] is None
    assert not any(c["settled"] for c in p["carried"])


def test_a_row_missing_a_component_is_not_scored_as_zero(tree):
    web, _, _ = tree
    _empty_current(web)
    _write(web, "nfl/market/00-0000005/2026-2.json", _at("00-0000005", "E Five", 9, 2))
    _season(web, "00-0000005", {2: {"rec": 4}})                   # rec_yds not recorded
    p, _ = fb(tree)
    assert p["featured_ladder"]["result"]["value"] == 4             # the ladder's stat is there
    assert p["fantasy"]["result"] is None                          # the points are not


def test_a_pending_lean_is_not_published_and_a_void_one_is(tree):
    web, board, _ = tree
    _empty_current(web)
    _write(web, "nfl/market/00-0000005/2026-2.json", _at("00-0000005", "E Five", 9, 2))
    _ledger(board, [("published", "l1", "00-0000005", 2, 4.5, "under", None, None,
                     "2026-09-18T00:00:00Z")])
    p, _ = fb(tree)
    assert p["featured_ladder"]["lean"] is None
    _ledger(board, [("published", "l1", "00-0000005", 2, 4.5, "under", None, None,
                     "2026-09-18T00:00:00Z"),
                    ("void", "l1", "00-0000005", 2, 4.5, "under", None, None,
                     "2026-09-18T00:00:00Z")])
    p, _ = fb(tree)
    assert p["featured_ladder"]["lean"]["status"] == "void"
    assert p["featured_ladder"]["lean"]["result"] is None


def test_two_settlements_that_disagree_withhold_the_result(tree):
    _two_candidates(tree, a_rec=2, b_rec=9, a_lean_result="missed", b_lean_result="cleared")
    web, board, _ = tree
    _season(web, "00-0000007", {2: {"rec": 3, "rec_yds": 30}})     # the ledger says 2
    p, _ = fb(tree)
    assert p["featured_ladder"]["result"] is None
    assert p["featured_ladder"]["lean"]["actual"] == 2.0
    assert "featured_ladder.result" in _parts(p)


def test_verify_refuses_a_tampered_result(tree):
    _two_candidates(tree, a_rec=2, b_rec=9, a_lean_result="missed", b_lean_result="cleared")
    p, files = fb(tree)
    assert L.verify(p, files).clean
    p["featured_ladder"]["lean"]["result"] = "cleared"
    assert not L.verify(p, files).clean
    p, files = fb(tree)
    p["featured_ladder"]["result"]["value"] = 9
    assert not L.verify(p, files).clean
    p, files = fb(tree)
    p["fantasy"]["presets"][0]["result"]["points"] = 12.0
    assert not L.verify(p, files).clean
    p, files = fb(tree)
    p["distributions"]["players"][0]["result"]["points"] = 12.0
    assert not L.verify(p, files).clean


def test_the_archive_carries_the_current_period_after_the_export_deletes_it(tree, tmp_path):
    web, _, _ = tree
    archive = str(tmp_path / "archive")
    # The refresh that published week 3: its files go into the archive.
    files, _ = L.load_inputs(web, None, None, archive)
    assert L.archive_current(files, archive) == (3, 0)
    assert L.archive_current(files, archive) == (0, 3)              # idempotent
    # The Monday refresh: the export has deleted them.
    _empty_current(web)
    p, files = fb(tree, archive=archive)
    fl = p["featured_ladder"]
    assert fl["player"]["id"] == "00-0000002" and len(fl["rungs"]) == 10
    assert fl["provenance"]["carried"] is True and fl["provenance"]["walked"] == 0
    assert fl["provenance"]["period"]["key"] == PKEY
    assert "last published" in fl["provenance"]["reason"]
    assert fl["source"]["key"] == "server:landing-archive/nfl/market/00-0000002/2026-3.json"
    assert L.verify(p, files).clean
    # Without the archive the same tree is dark - the archive is what carried it.
    p, _ = fb(tree, archive=None)
    assert p["featured_ladder"] is None


def test_a_served_file_wins_over_its_archived_copy(tree, tmp_path):
    web, _, _ = tree
    archive = str(tmp_path / "archive")
    _write(archive, "nfl/market/00-0000005/2026-2.json", _at("00-0000005", "E Five", 4, 2))
    _empty_current(web)
    _write(web, "nfl/market/00-0000005/2026-2.json", _at("00-0000005", "E Five", 9, 2))
    p, _ = fb(tree, archive=archive)
    assert len(p["featured_ladder"]["rungs"]) == 9
    assert p["featured_ladder"]["source"]["key"] == "nfl/market/00-0000005/2026-2.json"


def test_the_archive_never_removes_a_file(tree, tmp_path):
    web, _, _ = tree
    archive = str(tmp_path / "archive")
    files, _ = L.load_inputs(web, None, None, archive)
    L.archive_current(files, archive)
    os.remove(os.path.join(web, "nfl", "market", "00-0000001", f"{PKEY}.json"))
    files, _ = L.load_inputs(web, None, None, archive)
    L.archive_current(files, archive)
    assert os.path.isfile(os.path.join(archive, "nfl", "market", "00-0000001", f"{PKEY}.json"))


def test_the_report_line_names_the_carried_period(tree):
    web, _, _ = tree
    p, _ = built(tree)                         # the fixture as it is: nothing carried
    assert L.carried_lines(p)[0].startswith("carried: none")
    _empty_current(web)
    _write(web, "nfl/market/00-0000005/2026-2.json", _at("00-0000005", "E Five", 9, 2))
    p, _ = fb(tree)
    lines = L.carried_lines(p)
    assert len(lines) == 3 and all("Week 2" in x and "2026-2" in x for x in lines)
    assert all("NOT on disk" in x for x in lines)


def test_scoring_rounds_half_up_like_the_site():
    assert L.score({"rec": 0.125}, {"rec": 1}) == 0.13          # round() would give 0.12
    assert L.score({"rec": 3, "rec_yds": None}, {"rec": 1, "rec_yds": 0.1}) == 3.0
