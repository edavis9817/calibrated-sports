"""f-26: the attack primitives and the target selector return BOTH answers.

A reliability check that can only pass is the thing this unit exists to catch,
so every test here drives a primitive to the answer it is not expected to give.
No store, no network: synthetic rows and a tmp_path relay.
"""
import importlib.util
import json
import os

import numpy as np
import pytest

HERE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research", "f26_reliability")


def _load(name):
    spec = importlib.util.spec_from_file_location("f26_" + name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


L = _load("f26lib")
SEL = _load("select")


def clustered(n_blocks=60, per=8, seed=0):
    """Rows that share a block effect: 8 rungs settling off one score."""
    rng = np.random.default_rng(seed)
    eff = rng.normal(0.3, 1.0, n_blocks)
    v = np.concatenate([e + rng.normal(0, 0.2, per) for e in eff])
    return v, [b for b in range(n_blocks) for _ in range(per)]


def test_block_bootstrap_is_wider_than_iid_on_clustered_rows():
    v, lab = clustered()
    stat = lambda idx: float(v[idx].mean())  # noqa: E731
    blocked = L.summ(stat(np.arange(len(v))), L.block_boot(stat, L.blocks_of(lab), 500, 1))
    iid = L.summ(blocked["est"], L.iid_boot(stat, len(v), 500, 1))
    assert iid["width"] < 0.6 * blocked["width"]


def test_the_vacuous_duplication_is_retired_and_every_call_site_is_gone():
    """f-27. It resampled with the attacker's own bootstrap, so it could not fail. The
    name raises, and no adapter may call it or print a pass off the descriptive ratio."""
    import ast
    v, lab = clustered()
    with pytest.raises(L.VacuousCheck):
        L.duplication(lambda idx: float(v[idx].mean()), len(v), lab)
    ic = L.iid_contrast(lambda idx: float(v[idx].mean()), len(v), lab, draws=400, seed=2)
    assert "passes" not in ic and ic["iid_over_blocked_width"] < 0.6
    adapters = [f for f in os.listdir(HERE) if f.startswith("attack_") and f.endswith(".py")]
    assert len(adapters) >= 5
    for f in adapters:
        tree = ast.parse(open(os.path.join(HERE, f), encoding="utf-8").read())
        calls = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        assert "duplication" not in calls, f                 # by AST: the docstrings name it on purpose
        assert "duplication_through" in calls, f             # every adapter runs the real one


def test_selfcheck_every_step_rejects_its_plant():
    assert _load("selfcheck").main() == 0


def test_duplication_through_catches_a_bootstrap_that_ignores_blocks():
    v, lab = clustered()
    rows = [{"game": g, "v": float(x)} for g, x in zip(lab, v)]

    def honours_blocks(rws):
        vv = np.array([r["v"] for r in rws])
        b = L.blocks_of([r["game"] for r in rws])
        d = L.block_boot(lambda idx: float(vv[idx].mean()), b, 300, 3)
        return float(np.percentile(d, 97.5) - np.percentile(d, 2.5))

    def ignores_blocks(rws):
        vv = np.array([r["v"] for r in rws])
        d = L.iid_boot(lambda idx: float(vv[idx].mean()), len(vv), 300, 3)
        return float(np.percentile(d, 97.5) - np.percentile(d, 2.5))

    units = len(set(lab))
    good = L.duplication_through(honours_blocks, rows, "game", fn_name="blocked", units=units)
    bad = L.duplication_through(ignores_blocks, rows, "game", fn_name="rows", units=units)
    assert good["passes"] and good["discriminates"] and good["verdict"] == "honours_blocks" and good["survives"]
    assert not bad["passes"] and bad["verdict"] == "NARROWS" and not bad["survives"]
    # a row bootstrap is a game bootstrap when there is one row a game - and only then
    one = rows[::8]
    solo = L.duplication_through(ignores_blocks, one, "game", fn_name="rows", units=len(one))
    assert solo["verdict"] == "row_bootstrap_one_per_unit" and solo["survives"]
    with pytest.raises(ValueError):
        L.duplication_through(honours_blocks, rows, "game")              # no fn_name, no units: refused
    assert L.require_through(["a"], {"a": good}) == {"a": "honours_blocks"}
    with pytest.raises(SystemExit):
        L.require_through(["a", "b"], {"a": good})                       # a claim with no through result


def test_reproduce_is_at_the_stated_precision_and_can_fail():
    assert L.reproduce("x", -0.002668, -0.0027, 4)["reproduces"]
    assert not L.reproduce("x", -0.0189, -0.026, 3)["reproduces"]          # c-32's total, after the pace rebuild
    assert L.same_within_mc(0.0861, 0.084, 0.0293)["consistent"]
    assert not L.same_within_mc(0.131, 0.112, 0.0725)["consistent"]


def test_multiplicity_survives_and_does_not():
    assert L.multiplicity(0.0010, 0.00024, (72,))["bonferroni"][72]["survives_0.05"]      # c-30 on 3
    assert not L.multiplicity(0.0009, 0.00033, (72,))["bonferroni"][72]["survives_0.05"]  # c-30 pooled


def test_multiplicity_degenerate_intervals_enter_at_p_one_and_k_is_a_real_count():
    assert not L.multiplicity(0.05, 0.0, (1,))["bonferroni"][1]["survives_0.05"]
    assert not L.multiplicity(0.05, 1e-9, (1,), n_blocks=4)["bonferroni"][1]["survives_0.05"]
    assert L.multiplicity(0.05, 0.01, (1,), n_blocks=32)["bonferroni"][1]["survives_0.05"]
    with pytest.raises(ValueError):
        L.multiplicity(0.05, 0.01, (0,))
    assert L.registered_count({"registered_intervals": {"count": 72}}, "t") == 72
    with pytest.raises(SystemExit):
        L.registered_count({}, "t")


def test_alt_blocks_does_not_read_fewer_than_five_blocks():
    v, lab = clustered()
    r = L.alt_blocks(lambda idx: float(v[idx].mean()), len(v), {"four": [g % 4 for g in lab], "game": lab}, draws=200)
    assert r["four"]["excludes_zero"] is None and not r["four"]["read"]
    assert r["game"]["read"] and r["game"]["excludes_zero"] in (True, False)
    assert "NOT READ" in L.fmt(r["four"])


def test_mde_claim_can_contradict_the_target():
    assert L.mde_claim(0.203, 0.0725)["consistent"]
    assert not L.mde_claim(0.10, 0.0725)["consistent"]
    assert not L.mde_claim(None, 0.0725)["consistent"]


def test_mde_has_three_readings_and_a_null_is_not_called_at_its_mde():
    assert L.mde_ratio(0.050, 0.039)["reading"] == "below"
    assert L.mde_ratio(0.104, 0.034)["reading"] == "at"
    assert L.mde_ratio(-0.0027, 0.00046)["reading"] == "clear"
    assert not L.mde_ratio(0.050, 0.039)["at_mde"]


# ------------------------------------------------------------------ the selector

def _relay(tmp_path, reports, runlog):
    (tmp_path / "reports" / "json").mkdir(parents=True)
    (tmp_path / "state").mkdir()
    for u, d in reports.items():
        base = {"unit_id": u, "track": u[0], "summary": "", "findings": [], "verified_by": [], "files_changed": [],
                "needs_ethan": []}
        base.update(d)
        (tmp_path / "reports" / "json" / (u + ".json")).write_text(json.dumps(base), encoding="utf-8")
    (tmp_path / "runlog.jsonl").write_text(
        "\n".join(json.dumps({"unit": u, "ts": ts}) for u, ts in runlog.items()), encoding="utf-8")
    return str(tmp_path)


REPORTS = {
    "c-90": {"summary": "slope +0.104 [+0.039, +0.176], MDE 0.10"},
    "c-91": {"summary": "dBrier -0.0075 [-0.0090, -0.0060] against the league"},
    "a-90": {"summary": "published c-91's record", "files_changed": ["jobs/game_export.py"]},
    "b-90": {"summary": "a page, no numbers", "files_changed": ["app/x/page.tsx"]},
    "c-80": {"summary": "old result +0.5 [+0.1, +0.9]"},
    "f-23": {"summary": "attack +0.0004 [+0.0001, +0.0008]"},
}
RUNLOG = {"c-90": "2026-10-02T01:00:00-04:00", "c-91": "2026-10-01T01:00:00-04:00",
          "a-90": "2026-10-03T01:00:00-04:00", "b-90": "2026-10-03T02:00:00-04:00",
          "c-80": "2026-09-20T01:00:00-04:00", "f-23": "2026-10-04T01:00:00-04:00"}


def test_rank_puts_the_published_claim_first_and_excludes_what_it_should(tmp_path):
    relay = _relay(tmp_path, REPORTS, RUNLOG)
    reports, bad = SEL.load_reports(relay)
    win, scored, no_claim = SEL.rank(reports, SEL.load_marker(relay))
    assert not bad
    assert "c-80" not in win                       # finished before the window opened
    assert "f-23" not in win                       # an attack unit is not a target
    assert [x["unit"] for x in scored] == ["c-91", "c-90"]     # published (3+1) outranks MDE-only (1+1)
    assert scored[0]["score"] == 4 and "a-90" in scored[0]["why"][0]
    assert set(no_claim) == {"a-90", "b-90"}       # no interval stated: listed, never ranked


def test_a_verdict_removes_a_unit_and_an_unreached_one_stays(tmp_path):
    relay = _relay(tmp_path, REPORTS, RUNLOG)
    v = tmp_path / "v.json"
    v.write_text(json.dumps({"run_id": "t1", "at": "now", "verdicts": {"c-91": "citable"}}), encoding="utf-8")
    assert SEL.main(["select.py", relay, "--record", str(v)]) == 0
    reports, _ = SEL.load_reports(relay)
    marker = SEL.load_marker(relay)
    win, scored, _ = SEL.rank(reports, marker)
    assert "c-91" not in win and [x["unit"] for x in scored] == ["c-90"]
    assert marker["runs"][0]["ranked_not_reached"] == ["c-90"]
    assert marker["window_start"] == SEL.WINDOW_START          # the window never moves


def test_the_window_can_come_back_empty(tmp_path):
    relay = _relay(tmp_path, {"c-80": REPORTS["c-80"]}, {"c-80": RUNLOG["c-80"]})
    reports, _ = SEL.load_reports(relay)
    win, scored, no_claim = SEL.rank(reports, SEL.load_marker(relay))
    assert win == [] and scored == [] and no_claim == []


def test_record_refuses_a_verdict_for_a_unit_with_no_report(tmp_path):
    relay = _relay(tmp_path, REPORTS, RUNLOG)
    v = tmp_path / "v.json"
    v.write_text(json.dumps({"run_id": "t1", "at": "now", "verdicts": {"z-99": "citable"}}), encoding="utf-8")
    with pytest.raises(SystemExit):
        SEL.main(["select.py", relay, "--record", str(v)])
    assert not os.path.exists(SEL.marker_path(relay))


def test_needs_ethan_repeat_keys_on_the_subject_not_the_verb(tmp_path):
    reps = {
        "a-01": {"needs_ethan": [{"what": "Merge order for c-24 and c-27"},
                                 {"what": "Repoint Weekly Refresh at the production clone"}]},
        "b-01": {"needs_ethan": [{"what": "Merge order for b-94"},
                                 {"what": "Repoint Weekly Refresh at the production clone, and decide the slug commit"}]},
    }
    relay = _relay(tmp_path, reps, {"a-01": "2026-10-01T01:00:00-04:00", "b-01": "2026-10-02T01:00:00-04:00"})
    reports, _ = SEL.load_reports(relay)
    rows = {r["what"]: r for r in SEL.needs_ethan(reports, ["b-01"])}
    assert not rows["Merge order for b-94"]["repeat"]           # same verb, different branch
    assert rows["Repoint Weekly Refresh at the production clone, and decide the slug commit"]["repeat"]
    assert SEL.needs_ethan(reports, ["a-01"])[0]["earlier_unit"] is None     # nothing earlier to repeat
