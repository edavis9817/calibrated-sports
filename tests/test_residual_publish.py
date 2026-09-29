"""a-51: publishing the opportunity residual - the ids a page reads, the registry
rows that pin each one's range to one owner, and the Sources gate on the
analytics files.

Runs without a store. The real-data check is `jobs.publish_preflight`, which
publishes into a scratch analytics.db, builds the analytics tree from it and runs
`analytics.export.gate` on that tree.
"""
import copy

import pytest

from analytics import export as AX
from analytics import residual as R
from jobs import metric_registry as M
from jobs import source_registry as S
from tests.test_metric_registry import analytics_fixture

FAMILIES = ("player", "per_game", "fit", "bands", "persistence")


def test_the_ids_are_the_producers_own_and_asof_is_held():
    ids = M.residual_metric_ids()
    assert len(ids) == len(set(ids)) == 25
    # read off metric_for, the one definition, for every published family
    assert set(ids) == {R.metric_for(s, f).key for f in FAMILIES for s in R.PAIRINGS}
    # the a-33 decision: the five as-of keys stay out
    assert not [i for i in ids if ".asof." in i]
    assert {R.metric_for(s, "asof").key for s in R.PAIRINGS}.isdisjoint(ids)
    # the ids b-67's panels read
    assert {"opportunity_residual.per_game.%s" % s for s in R.PAIRINGS} <= set(ids)
    assert {"opportunity_residual.persistence.%s" % s for s in R.PAIRINGS} <= set(ids)


def test_each_id_is_served_where_analytics_export_writes_it():
    for mid in M.residual_metric_ids():
        assert M.analytics_file(mid) == "%s/%s.json" % (AX.PREFIX, mid)
        assert M.analytics_file(mid) == "analytics/nfl/%s.json" % mid


def test_two_rows_per_metric_owned_by_the_file_copied_by_the_index():
    rows = {m["id"]: m for m in M.ANALYTICS_METRICS}
    assert len(rows) == 50
    for mid in M.residual_metric_ids():
        for end in ("season_from", "season_to"):
            m = rows["%s.%s" % (mid, end)]
            assert m["source"] == {"file": M.analytics_file(mid), "path": end}
            assert m["copies"] == [{"file": M.ANALYTICS_INDEX,
                                    "path": "metrics[metric=%s].%s" % (mid, end)}]
    # and the manifest exports them, so the site resolves them by id
    assert {m["id"] for m in M.manifest_block("a published stat")} >= set(rows)


def test_the_gate_passes_an_agreeing_tree():
    out = analytics_fixture()
    lines = AX.gate(out)
    assert "50 metrics, 100 locations resolved" in lines[0]
    assert "0 undeclared disagreements" in lines[0]
    assert "25 metric files" in lines[1] and "nflverse.stats" in lines[1]


def test_the_gate_refuses_an_index_that_disagrees_with_its_file():
    out = analytics_fixture()
    idx = copy.deepcopy(out[M.ANALYTICS_INDEX])
    idx["metrics"][3]["season_to"] = 2024
    out[M.ANALYTICS_INDEX] = idx
    with pytest.raises(M.MetricDisagreement, match="season_to"):
        AX.gate(out)


def test_a_store_without_the_residual_refuses_rather_than_skipping():
    out = analytics_fixture()
    del out[M.analytics_file("opportunity_residual.per_game.receptions")]
    with pytest.raises(M.MetricDisagreement, match="was not produced"):
        AX.gate(out)
    # and an index entry missing is the same failure
    out = analytics_fixture()
    out[M.ANALYTICS_INDEX] = {"metrics": out[M.ANALYTICS_INDEX]["metrics"][1:]}
    with pytest.raises(M.MetricDisagreement, match="matched 0"):
        AX.gate(out)


# ------------------------------------------------------------------ Sources

def test_a_weekly_stats_metric_is_named_and_was_not_before():
    f = {"k": {"kind": "analytics.metric", "basis": "weekly_stats",
               "requires": ["weekly_stats.targets", "weekly_stats.position"]}}
    probs, seen = S.analytics_problems(f)
    assert seen == 1 and probs == []
    # discriminates: the pre-a-51 declaration (pbp, participation, ngs) refuses it
    old = dict(S.DECLARED["nfl"])
    try:
        S.DECLARED["nfl"]["analytics.metric"] = ("nflverse.pbp", "nflverse.participation",
                                                 "nflverse.ngs")
        probs, _ = S.analytics_problems(f)
        assert probs and "nflverse.stats" in probs[0]
    finally:
        S.DECLARED["nfl"].clear()
        S.DECLARED["nfl"].update(old)


def test_a_pbp_metric_reading_positions_names_both_sources():
    f = {"k": {"kind": "analytics.metric", "basis": "pbp",
               "requires": ["pbp.rusher_player_id|rush_attempt", "weekly_stats.position"]}}
    _statement, used = S.require_analytics(f)
    assert used == ["nflverse.pbp", "nflverse.stats"]


def test_an_unmapped_dataset_refuses():
    f = {"k": {"kind": "analytics.metric", "basis": "snap_counts", "requires": []}}
    with pytest.raises(S.SourceRegistryError, match="snap_counts"):
        S.require_analytics(f)


def test_an_empty_check_is_not_a_pass():
    with pytest.raises(S.SourceRegistryError, match="empty check"):
        S.require_analytics({"analytics/nfl/index.json": {"kind": "analytics.index"}})


def test_every_mapped_source_is_registered_for_nfl():
    for sid in S.ANALYTICS_DATASETS.values():
        assert sid in S.SOURCES and "nfl" in S.SOURCES[sid]["sports"]
        assert sid in S.DECLARED["nfl"]["analytics.metric"]
        assert sid in S.DECLARED["nfl"]["analytics.index"]


def test_every_residual_requires_dataset_is_mapped():
    # the datasets the residual's own `requires` names, plus each basis it can take
    ds = {r[0] for reqs in R.REQUIRES.values() for r in reqs} | {"pbp", "weekly_stats"}
    assert ds <= set(S.ANALYTICS_DATASETS)


# ------------------------------------------------------------------ the band method tag

def test_the_band_tag_states_players_at_095():
    m = AX.describe_method("boot2000-players", "player")
    assert m["coverage_level"] == 0.95
    assert "resampling players" in m["interval"] and "2,000" in m["interval"]


def test_a_boot_tag_naming_another_unit_than_the_block_refuses():
    with pytest.raises(AX.MethodError, match="blocked on"):
        AX.describe_method("boot2000-players", "game")
    with pytest.raises(AX.MethodError):
        AX.describe_method("boot2000", "player")


def test_the_band_interval_is_the_literal_95_percentile_pair():
    # The 0.95 the rule states is only true while band_values cuts at 2.5/97.5.
    import ast
    import inspect
    src = inspect.getsource(R.band_values)
    pairs = [[c.value for c in n.elts] for n in ast.walk(ast.parse(src))
             if isinstance(n, ast.List) and len(n.elts) == 2
             and all(isinstance(c, ast.Constant) for c in n.elts)]
    assert pairs == [[2.5, 97.5]]
    assert "boot%d-players" in src
