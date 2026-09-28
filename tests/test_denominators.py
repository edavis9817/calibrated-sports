"""Three denominators (a-46, DECISIONS-2026-09-28 §P): a weekly count is never
divided by the archive.

The unit tests drive `jobs.denominators.compute` on a hand-built slate whose
every count is worked out below, so each assertion can only be satisfied by the
tier it names. The export tests run the real exporter over test_export_web's
store fixture and read the written manifest.
"""
import copy
import json

import pytest

from jobs import denominators as D
from jobs import export_web as E
from jobs import metric_registry as M
from tests.test_export_web import NOW, _walk, db  # noqa: F401  (db is a fixture)

CFG = D.Participation(noun="offensive usage", definition="a target, carry or attempt",
                      columns=("targets", "carries"), scope_season_types=("REG",),
                      id_col="pid", period_col="wk", team_col="team")
T = 1_000_000.0
CURRENT = {"season": 2026, "period": {"index": 3, "label": "Week 3", "key": "2026-3"}}


def row(pid, season, wk, team, tgt=0, car=0, st="REG"):
    return {"pid": pid, "season": season, "wk": wk, "team": team, "season_type": st,
            "targets": tgt, "carries": car}


def game(season, wk, a, b, final, kick):
    return {"season": season, "period": wk, "final": final, "kickoff_ts": kick, "teams": (a, b)}


# A 2025 archive season, two 2026 weeks final, week 3 part-played:
#   AAA-BBB final (wk3), CCC-DDD open, EEE-FFF kicked, no final score.
GAMES = [
    game(2025, 1, "AAA", "CCC", True, T - 9e7),
    game(2026, 1, "AAA", "BBB", True, T - 2e6), game(2026, 1, "CCC", "DDD", True, T - 2e6),
    game(2026, 2, "AAA", "CCC", True, T - 1e6), game(2026, 2, "BBB", "DDD", True, T - 1e6),
    game(2026, 3, "AAA", "BBB", True, T - 1e4), game(2026, 3, "CCC", "DDD", False, T + 1e4),
    game(2026, 3, "EEE", "FFF", False, T - 100),
    game(2026, 4, "AAA", "DDD", False, T + 9e5),
]
ROWS = [
    row("old", 2025, 1, "AAA", tgt=5),                 # archive only
    row("c1", 2026, 1, "CCC", tgt=4), row("c1", 2026, 2, "CCC", tgt=6),
    row("c2", 2026, 2, "CCC", car=10),
    row("c3", 2026, 1, "CCC", tgt=2),                  # CCC's last game is wk2: not expected
    row("d1", 2026, 2, "DDD", tgt=3),
    row("d0", 2026, 2, "DDD"),                         # a row with no usage never counts
    row("a1", 2026, 3, "AAA", tgt=7), row("b1", 2026, 3, "BBB", car=12),
]
ARCHIVE = {"old", "c1", "c2", "c3", "d1", "a1", "b1"}


def compute(priced=("c1", "d1", "zz"), rows=ROWS, archive=ARCHIVE, now=T):
    return D.compute(CFG, rows, GAMES, CURRENT, archive, priced, now)


def test_each_tier_counts_its_own_span():
    t = compute()
    assert t["archive"]["players"] == 7
    assert t["archive"]["span"] == {"season_from": 2025, "season_to": 2026, "seasons": 2}
    assert t["archive"]["games"] == {"final": 6, "season_from": 2025, "season_to": 2026}
    assert t["season"]["players"] == 6            # everyone but "old"
    assert t["season"]["games"] == {"final": 5, "scheduled": 8}
    assert t["week"]["games"] == {"scheduled": 3, "final": 1, "open": 1, "awaiting_final": 1}


def test_the_week_denominator_is_who_is_expected_in_the_open_games():
    """CCC and DDD play the one open game. Their last final game is week 2:
    c1, c2 (CCC) and d1 (DDD) took part; c3 last played week 1; d0 had no usage."""
    w = compute()["week"]
    assert w["players"] == {"played": 2, "expected": 3, "priced": 3, "priced_expected": 2}
    assert w["share_priced"] == round(2 / 3, 4)


def test_played_and_priced_are_different_games():
    """A priced player has not played this period by construction: a1 and b1
    played week 3, and pricing them would put them outside `expected`."""
    w = compute(priced=("a1", "b1"))["week"]
    assert w["players"]["played"] == 2 and w["players"]["priced_expected"] == 0
    assert w["share_priced"] == 0.0


def test_only_the_week_tier_is_divisible_and_it_holds_the_only_share():
    t = compute()
    assert [k for k in ("archive", "season", "week") if t[k]["divisible"]] == ["week"]
    shares = [k for k in json.dumps(t).split('"') if k.startswith("share")]
    assert shares == ["share_priced"]


def test_no_open_game_publishes_no_share_rather_than_zero():
    t = compute(now=T + 2e4)                     # CCC-DDD has kicked off
    assert t["week"]["games"]["open"] == 0
    assert t["week"]["players"]["expected"] == 0
    assert t["week"]["share_priced"] is None


def test_the_share_cannot_exceed_one():
    t = compute(priced=("c1", "c2", "d1", "x", "y", "z"))
    assert t["week"]["players"]["priced"] == 6
    assert t["week"]["share_priced"] == 1.0


def test_a_player_the_archive_does_not_publish_is_in_no_tier():
    """e.g. excluded for having no resolvable name: not in the index, so not in
    the season count either - the season tier stays a subset of the archive."""
    t = compute(archive=ARCHIVE - {"c2"})
    assert (t["archive"]["players"], t["season"]["players"]) == (6, 5)
    # ...but `expected` is who takes part in the games, published or not
    assert t["week"]["players"]["expected"] == 3


def test_post_season_rows_are_outside_the_scoped_tiers_but_count_as_expected():
    rows = ROWS + [row("p1", 2026, 2, "DDD", tgt=1, st="POST")]
    t = compute(rows=rows)
    assert t["season"]["players"] == 6
    assert t["week"]["players"]["expected"] == 4


def test_the_nfl_participation_is_the_player_index_rule():
    """One definition drives player_scope and the tiers."""
    rows = [{"gsis_id": "x", "season_type": "REG", "targets": 0, "carries": 0, "attempts": 3},
            {"gsis_id": "y", "season_type": "REG", "targets": 0, "carries": 0, "attempts": 0},
            {"gsis_id": "z", "season_type": "POST", "targets": 4, "carries": 0, "attempts": 0}]
    assert E.player_scope(rows) == {"x"}
    assert [E.PARTICIPATION.used(r) for r in rows] == [True, False, True]


# ------------------------------------------------------------------ the export


def test_the_manifest_publishes_the_tiers_and_the_legacy_counts_agree(db):  # noqa: F811
    dest = str(db / "out")
    E.export(only=["players", "manifest"], now_ts=NOW, dest=dest)
    m = _walk(dest)["nfl/manifest.json"]
    d = m["denominators"]
    # store fixture: current period 2026 week 2, DET@BUF open. BUF's last final
    # game is 2026 week 1 at HOU, where 00-A and 00-S2 had usage and 00-D did
    # not; DET has no final game at all.
    assert d["week"]["span"]["period"]["key"] == "2026-2"
    assert d["week"]["games"] == {"scheduled": 1, "final": 0, "open": 1, "awaiting_final": 0}
    assert d["week"]["players"] == {"played": 0, "expected": 2, "priced": 0, "priced_expected": 0}
    assert d["week"]["share_priced"] == 0.0
    assert d["season"]["players"] == 2 and d["season"]["games"] == {"final": 1, "scheduled": 2}
    assert d["archive"]["players"] == m["counts"]["players"]
    assert d["archive"]["games"]["final"] == m["counts"]["games"] == 4
    assert d["participation"]["noun"] == "offensive usage"
    rep = M.check({M.MANIFEST: m}, M.MANIFEST_METRICS)
    assert rep.clean, rep.statement
    assert rep.checked == sum(1 + len(x["copies"]) for x in M.MANIFEST_METRICS)


def test_the_manifest_gate_refuses_a_legacy_count_that_drifts(db):  # noqa: F811
    """The discrimination test: the old unlabelled figure and its tier, both answers."""
    dest = str(db / "out")
    E.export(only=["players", "manifest"], now_ts=NOW, dest=dest)
    m = _walk(dest)["nfl/manifest.json"]
    bad = copy.deepcopy(m)
    bad["counts"]["players"] += 1
    rep = M.check({M.MANIFEST: bad}, M.MANIFEST_METRICS)
    assert not rep.clean and "coverage.players.archive" in rep.statement
    with pytest.raises(M.MetricDisagreement):
        M.require({M.MANIFEST: bad}, M.MANIFEST_METRICS)


def test_the_export_refuses_when_the_tiers_disagree_with_counts(db, monkeypatch):  # noqa: F811
    real = E.build_denominators

    def skewed(*a, **k):
        t = real(*a, **k)
        t["archive"]["players"] += 1
        return t
    monkeypatch.setattr(E, "build_denominators", skewed)
    dest = db / "out"
    with pytest.raises(M.MetricDisagreement, match="coverage.players.archive"):
        E.export(only=["manifest"], now_ts=NOW, dest=str(dest), log=lambda *a: None)
    assert not (dest / "nfl" / "manifest.json").exists()


def test_a_null_share_passes_the_gate_only_because_it_is_declared_nullable():
    """Off-season / every game kicked off: share_priced is null by design. The
    gate must pass it - and must still refuse a null on a metric not so declared."""
    t = compute(now=T + 2e4)
    m = {"counts": {"players": t["archive"]["players"], "teams": 0,
                    "market": t["week"]["players"]["priced"],
                    "games": t["archive"]["games"]["final"], "rungs": 0},
         "denominators": t}
    assert t["week"]["share_priced"] is None
    assert M.check({M.MANIFEST: m}, M.MANIFEST_METRICS).clean
    strict = [dict(x) for x in M.MANIFEST_METRICS]
    for x in strict:
        x.pop("nullable", None)
    rep = M.check({M.MANIFEST: m}, strict)
    assert not rep.clean and "share_priced" in rep.statement and "is null" in rep.statement
    assert [x["id"] for x in M.METRICS if x.get("nullable")] == [
        "coverage.players.week.share_priced"]


def test_every_metric_has_exactly_one_gate():
    # a-42 added the season gate, checked by jobs.season_export before it writes;
    # a-51 the analytics gate, checked by analytics.export.gate on the web path.
    r, m, s, a = (M.RESEARCH_METRICS, M.MANIFEST_METRICS, M.SEASON_METRICS,
                  M.ANALYTICS_METRICS)
    assert len(r) + len(m) + len(s) + len(a) == len(M.METRICS)
    ids = [{x["id"] for x in g} for g in (r, m, s, a)]
    assert sum(len(i) for i in ids) == len(set().union(*ids))
    assert m, "the manifest gate checks nothing"
