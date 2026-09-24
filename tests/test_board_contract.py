"""a-31: the Board becomes a published kind - contract, sources, uploader, cadence.

Run: pytest -q tests/test_board_contract.py

Every guard here is shown firing on the other input before it is trusted. The
one the brief named as the worst possible bug in this unit - an append-only
ledger vanishing from the bucket because a run did not re-declare it - has its
own section, and it is attacked from every direction the uploader offers: no
declaration, an empty one, a declaration reaching `board/`, a declaration of
every other prefix, a lost local tree, and a shrunken or rewritten ledger.
"""
import ast
import copy
import io
import json
import os

import polars as pl
import pytest
from jsonschema import Draft202012Validator

import config
from core import board as B
from jobs import export_web as E
from jobs import source_registry as R
from tests.test_board import H, K_DET, K_GB, T0, env, read, snapshot  # noqa: F401
from tests.test_export_web import FakeS3, creds  # noqa: F401

X = E.CONTRACT["x-contract"]
DEFS = E.CONTRACT["$defs"]


@pytest.fixture(autouse=True)
def _fresh_runtime_ledger():
    """A Board read watches its connection and leaves the table set it saw in
    the registry's per-sport ledger; a test that plants an unmapped read must
    not leak it into the next test's gate."""
    yield
    R._READS.pop("nfl", None)


def validator(name):
    return Draft202012Validator({"$ref": f"#/$defs/{name}", "$defs": DEFS})


def errors(name, obj):
    return [e.message for e in validator(name).iter_errors(obj)]


# ================================================================== the contract

def test_both_kinds_are_in_the_contract_and_route_by_key():
    assert X["kinds"]["board_index"] == "BoardIndexFile"
    assert X["kinds"]["board_read"] == "BoardReadFile"
    assert "board_index" not in X["sportless_kinds"] and "board_read" not in X["sportless_kinds"]
    assert E.kind_for_key("board/nfl/2026/wk03/index.json") == ("board_index", "sport")
    assert E.kind_for_key("board/nfl/2026/wk03/read-2026-09-23T160000Z.json") == ("board_read", "sport")


@pytest.mark.parametrize("key", [
    "board/nfl/2026/wk3/index.json",                          # a-26 pads the week
    "board/nfl/2026/wk03/read-2026-09-23T16:00:00Z.json",     # colons are stripped in keys
    "board/nfl/2026/wk03/read-2026-09-23.json",
    "board/nfl/ledger.json",
    "board/index.json",
])
def test_near_miss_keys_route_nowhere(key):
    assert E.kind_for_key(key) == (None, None)


def test_the_job_names_keys_the_contract_routes():
    """The job's key builders and the contract's patterns are two statements of
    one shape; this is the check that they agree, on the real builders."""
    from jobs import board_read as J
    assert E.kind_for_key(J.index_key(2026, 3))[0] == "board_index"
    assert E.kind_for_key(J.read_key(2026, 3, "2026-09-23T16:00:00Z"))[0] == "board_read"
    assert E.table_for_key("board/nfl/ledger.parquet")[0] == "board_ledger"
    assert E.table_for_key("board/nfl/ledger.csv")[0] == "board_ledger"
    assert E.table_for_key("board/nfl/ledger.xlsx") == (None, None)


def test_no_board_key_resolves_to_two_kinds():
    keys = ["board/nfl/2026/wk03/index.json", "board/nfl/2026/wk03/read-2026-09-23T160000Z.json"]
    for k in keys:
        hits = [e["kind"] for e in X["keys"] if __import__("re").match(e["pattern"], k)]
        assert len(hits) == 1, (k, hits)


# ------------------------------------------------ every estimate carries its interval and n

GOOD_BAND = {"lo_pp": 8.0, "hi_pp": None, "n": 100, "games": 40, "cleared": 0.48,
             "ci": [0.38, 0.58], "ci_method": "wilson_95", "roi": -0.08, "roi_ci": [-0.2, 0.05],
             "roi_ci_method": "game_block_bootstrap_2000", "source": "research/board_bands.py"}
GOOD_RATE = {"k": 6, "n": 10, "ci": [0.31, 0.83]}
GOOD_STREAK = {"rule": "L5", "k": 5, "n": 5, "display_rate": 1.0, "next_rate": 0.57,
               "next_ci": [0.556, 0.581], "next_n": 7452, "source": "F11", "line_basis": "posted"}
GOOD_VERDICT = {"id": "R15", "season": 2025, "brier_gap": 0.0195, "lo": 0.0144, "hi": 0.0253,
                "games": 284, "source": "research/walkforward.py"}


@pytest.mark.parametrize("name, good, field, bad", [
    ("BoardBand", GOOD_BAND, "ci", None),
    ("BoardBand", GOOD_BAND, "n", 0),
    ("BoardBand", GOOD_BAND, "games", None),
    ("BoardBand", GOOD_BAND, "roi_ci", None),
    ("BoardBand", GOOD_BAND, "cleared", None),
    ("BoardRate", GOOD_RATE, "ci", None),
    ("BoardRate", GOOD_RATE, "n", 0),
    ("BoardRate", GOOD_RATE, "n", 2.5),
    ("BoardStreak", GOOD_STREAK, "next_ci", None),
    ("BoardStreak", GOOD_STREAK, "next_n", None),
    ("BoardStreak", GOOD_STREAK, "next_rate", None),
    ("BoardVerdict", GOOD_VERDICT, "games", 0),
    ("BoardVerdict", GOOD_VERDICT, "lo", None),
])
def test_an_estimate_without_its_interval_or_sample_is_refused(name, good, field, bad):
    assert errors(name, good) == [], "the good shape must pass, or the refusal proves nothing"
    assert errors(name, dict(good, **{field: bad})), (name, field, bad)


def test_a_missing_estimate_is_null_on_the_row_never_an_empty_record():
    rates = {"last10": None, "season": None, "career": GOOD_RATE, "career_posted": None}
    assert errors("BoardRates", rates) == []
    assert errors("BoardRates", dict(rates, season={"k": 0, "n": 0, "ci": None}))


def test_the_job_emits_null_not_an_empty_record(env):
    """The producer side of the same rule, on the real functions."""
    J = env["J"]
    assert J.rates([], 5.5, 2026) == {"last10": None, "season": None, "career": None}
    counts = {"lean with no walk-forward band": 0, "streak with no F11 next-game rate": 0}
    assert J.band_payload({"bands": {}}, "receptions", 9.0, counts) is None
    assert counts["lean with no walk-forward band"] == 1
    band = J.band_payload({"bands": {"receptions|8+": dict(GOOD_BAND, k=48)}}, "receptions", 9.0)
    assert errors("BoardBand", band) == []
    hist = [{"value": 9, "away": False, "opp": "X"}] * 5
    assert J.streak_payload({"next_rates": {}}, "receptions", hist, 5.5, False, "Y", counts) is None
    assert counts["streak with no F11 next-game rate"] == 1


def test_void_and_its_reason_come_together_and_pulled_at_only_on_a_pull(env):
    rows, _ = _scenario_first_two_reads(env)
    row = rows["00-KP:receptions"]
    assert errors("BoardRow", row) == []
    assert errors("BoardRow", dict(row, void_reason=None))              # void with no reason
    assert errors("BoardRow", dict(row, pulled_at=None))                # a pull with no time
    up = rows["00-DL:receptions"]
    assert errors("BoardRow", up) == []
    assert errors("BoardRow", dict(up, void_reason="inactive"))         # a reason with no void
    assert errors("BoardRow", dict(up, pulled_at=B.iso(T0)))


def test_a_row_with_no_lean_carries_no_band():
    s = copy.deepcopy(DEFS["BoardRow"]["allOf"][2])
    v = Draft202012Validator(s)
    assert v.is_valid({"lean": None, "band": None, "lean_result": None})
    assert not v.is_valid({"lean": None, "band": GOOD_BAND, "lean_result": None})
    assert v.is_valid({"lean": "over", "band": GOOD_BAND, "lean_result": None})


# ------------------------------------------------------------- the ledger table

def test_the_ledger_columns_and_types_are_the_contracts():
    from jobs import board_read as J
    schema = J.ledger_schema()
    assert list(schema) == list(B.LEDGER_COLUMNS)
    assert schema["season"] == pl.Int64 and schema["line"] == pl.Float64
    assert schema["event"] == pl.Utf8
    entry = E.TABLES["board_ledger"]
    assert entry["append_only"] is True and entry["sources_as"] in X["kinds"]


def test_ledger_drift_between_job_and_contract_is_refused(monkeypatch):
    from jobs import board_read as J
    monkeypatch.setattr(B, "LEDGER_COLUMNS", B.LEDGER_COLUMNS + ("new_col",))
    with pytest.raises(E.ContractError, match="columns drifted"):
        J.ledger_schema()
    monkeypatch.undo()
    monkeypatch.setitem(B.LEDGER_DTYPES, "season", "float64")
    with pytest.raises(E.ContractError, match="types drifted"):
        J.ledger_schema()


# ================================================================== the sources

def test_the_board_declares_the_three_sources_the_brief_names():
    for kind in ("board_read", "board_index"):
        ids = set(R.DECLARED["nfl"][kind])
        assert {"oddsapi", "kalshi.ladders", "calibrated.walkforward"} <= ids, (kind, ids)
    assert "polymarket" not in R.DECLARED["nfl"]["board_read"]       # narrowed: it reads none
    assert R.check_registry(X["kinds"])


def test_the_board_is_scanned_as_a_producer_and_a_planted_read_is_refused(tmp_path, monkeypatch):
    from tests.test_source_registry import _src, plant
    text = _src("jobs.board_read") + ('\n\ndef planted(con):\n'
                                      '    return con.execute("select x from nfl_injury_week")\n')
    try:
        problems = plant(tmp_path, monkeypatch, jobs__board_read=text)
        assert any("jobs.board_read.planted" in p for p in problems), problems
        with pytest.raises(R.SourceRegistryError):
            R.require_declared(["board_read"])
    finally:
        monkeypatch.undo()
        R.refresh()
    assert R.PROBLEMS["nfl"] == []


def test_a_cte_is_not_a_table_but_a_cte_shadowing_a_mapped_table_is_kept():
    assert R.cte_names("WITH pg AS (SELECT 1), ranked AS (SELECT * FROM pg) SELECT * FROM ranked") \
        == {"pg", "ranked"}
    assert R.cte_names("no sql here, with x as (") == set()
    import models.features as F
    t, _ = R.sql_tables(F._ranked_cte("receptions", (2025,)))
    assert "pg" in t                                  # the raw scan sees it ...
    assert "pg" not in R.scan("jobs.board_read")["model_prob"][0]    # ... the closure does not
    assert "nfl_games" in R._mapped_tables()          # so a CTE named nfl_games would be kept


def test_the_read_refuses_after_an_unmapped_read_and_writes_nothing(env, monkeypatch):
    """The runtime half: SQLite reports a read the scan could not see, and the
    gate refuses BEFORE the ledger or either JSON file is written."""
    J = env["J"]
    _first_snapshot()
    real = J.kalshi_mid

    def sneaky(con, *a):
        con.execute("SELECT COUNT(*) FROM source_health").fetchone()
        return real(con, *a)
    monkeypatch.setattr(J, "kalshi_mid", sneaky)
    with pytest.raises(R.SourceRegistryError, match="source_health"):
        J.run(2026, 3, env["dest"], read_ts=T0, log=lambda s: None)
    assert not os.path.exists(J.ledger_path(env["dest"]))
    assert E.local_keys(env["dest"], tables=True) == {}


def test_the_board_writes_through_sync_keys_and_owns_no_prefix():
    """By AST: run() calls export_web.sync_keys with an EMPTY LIST literal as its
    prefixes, so nothing on disk can be deleted by a Board read."""
    src = open(os.path.join(E.ROOT, "jobs", "board_read.py"), encoding="utf-8").read()
    run = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "run")
    calls = [c for c in ast.walk(run) if isinstance(c, ast.Call)
             and isinstance(c.func, ast.Attribute) and c.func.attr == "sync_keys"]
    assert len(calls) == 1
    prefixes = calls[0].args[2]
    assert isinstance(prefixes, ast.List) and prefixes.elts == []
    assert any(isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
               and c.func.attr == "watch" for c in ast.walk(run))


# ================================================================== the job writes the contract

def _first_snapshot():
    GB, DET = "ev-2026_03_ATL_GB", "ev-2026_03_NYJ_DET"
    snapshot(T0 - H, GB, [("player_receptions", "Kyle Pitts", 3.5, -110, -110, None),
                          ("player_receptions", "Drake London", 5.5, -110, -110, None)])
    snapshot(T0 - H, DET, [("player_receptions", "Amon-Ra St. Brown", 7.5, -105, -115, None)])


def _scenario_first_two_reads(env):
    _first_snapshot()
    read(env, T0)
    snapshot(T0 + 20 * H, "ev-2026_03_ATL_GB",
             [("player_receptions", "Drake London", 5.5, -110, -110, None)])
    snapshot(T0 + 20 * H, "ev-2026_03_NYJ_DET",
             [("player_receptions", "Amon-Ra St. Brown", 7.5, -105, -115, None)])
    return read(env, T0 + 21 * H)


def test_every_file_the_job_writes_validates_and_carries_the_envelope(env):
    rows, idx = _scenario_first_two_reads(env)
    assert idx["kind"] == "board_index" and idx["schema_version"] == 2 and idx["sport"] == "nfl"
    assert len(idx["reads"]) == 2
    s = env["J"].check_tree(env["dest"], log=lambda *_: None)
    assert s == {"keys": 5, "bytes": s["bytes"], "json": 3, "tables": 2} and s["bytes"] > 0


def test_check_refuses_a_tampered_tree_and_an_empty_one(env, tmp_path):
    J = env["J"]
    with pytest.raises(SystemExit, match="nothing to check"):
        J.check_tree(str(tmp_path / "empty"), log=lambda *_: None)
    _scenario_first_two_reads(env)
    idx_path = os.path.join(J.week_dir(env["dest"], 2026, 3), "index.json")
    doc = json.load(open(idx_path))
    doc["verdict"]["games"] = 0
    json.dump(doc, open(idx_path, "w"))
    with pytest.raises(E.ContractError, match="verdict/games"):
        J.check_tree(env["dest"], log=lambda *_: None)


# ================================================================== the uploader

def _board_tree(env):
    _scenario_first_two_reads(env)
    return env["dest"]


def _put_keys(s3):
    return {k for k in s3.puts if not k.startswith("_state/")}


def test_the_board_tree_uploads_json_and_the_ledger_with_their_types(env, creds):  # noqa: F811
    dest = _board_tree(env)
    s3 = FakeS3()
    r = E.upload(dest=dest, client=s3, log=lambda *_: None, tree="board")
    assert r["uploaded"] == 5 and r["deleted"] == 0 and r["tree"] == "board"
    assert s3.puts["board/nfl/ledger.parquet"]["type"] == "application/vnd.apache.parquet"
    assert s3.puts["board/nfl/ledger.csv"]["type"].startswith("text/csv")
    assert s3.puts["board/nfl/2026/wk03/index.json"]["cache"] == "public, max-age=60"
    assert E.BOARD_STATE_KEY in s3.puts and E.REMOTE_STATE_KEY not in s3.puts
    assert len(r["append_only"]) == 2 and all("first upload" in a for a in r["append_only"])


def test_the_board_tree_takes_no_declaration(env, creds):  # noqa: F811
    dest = _board_tree(env)
    for decl in ([], ["board/"], ["nfl/"]):
        with pytest.raises(ValueError, match="deletes nothing"):
            E.upload(dest=dest, client=FakeS3(), log=lambda *_: None, tree="board", refreshed=decl)


def test_the_board_tree_refuses_a_foreign_key(env, creds):  # noqa: F811
    dest = _board_tree(env)
    E.write_if_changed(E.local_path(dest, "nfl/manifest.json"), {"kind": "x"})
    with pytest.raises(ValueError, match="carries the Board only"):
        E.upload(dest=dest, client=FakeS3(), log=lambda *_: None, tree="board")


# ---- THE WORST BUG IN THIS FILE: a ledger that vanishes ----------------------------------

def test_a_ledger_is_never_deleted_from_the_bucket_by_absence(env, creds, tmp_path):  # noqa: F811
    dest = _board_tree(env)
    s3 = FakeS3()
    E.upload(dest=dest, client=s3, log=lambda *_: None, tree="board")
    ledger_keys = {"board/nfl/ledger.parquet", "board/nfl/ledger.csv"}
    assert ledger_keys <= set(s3.objects)

    # 1. the Board's own tree, the ledger gone locally, re-uploaded with no declaration
    for k in ledger_keys:
        os.remove(E.local_path(dest, k))
    r = E.upload(dest=dest, client=s3, log=lambda *_: None, tree="board")
    assert r["deleted"] == 0 and s3.deletes == [] and ledger_keys <= set(s3.objects)

    # 2. the WEB tree, holding the same upload record (the worst case: a record that
    # lists board keys), declaring every prefix it could - board/ itself refused ...
    web = str(tmp_path / "web")
    E.write_if_changed(E.local_path(web, "nfl/manifest.json"), {"kind": "x"})
    state = json.load(open(os.path.join(dest, E.STATE_FILE)))
    json.dump(state, open(os.path.join(web, E.STATE_FILE), "w"))
    for decl in (["board/"], ["b"], ["boa"], [""]):
        with pytest.raises(ValueError):
            E.upload(dest=web, client=s3, log=lambda *_: None, refreshed=decl)
    # ... and a declaration of everything ELSE deletes nothing under board/
    r = E.upload(dest=web, client=s3, log=lambda *_: None,
                 refreshed=["nfl/", "research/", "analytics/", "sports.json"])
    assert not any(k.startswith("board/") for k in s3.deletes), s3.deletes
    assert ledger_keys <= set(s3.objects)
    assert r["removed_withheld"] >= 2          # counted, not silently kept


def test_the_delete_guard_can_fire(env, creds, tmp_path):  # noqa: F811
    """The guard above must be discriminating: the SAME web upload, with a key
    that is NOT under board/, does delete it. Otherwise 'nothing was deleted'
    would pass on an uploader that deletes nothing at all."""
    web = str(tmp_path / "web")
    E.write_if_changed(E.local_path(web, "nfl/manifest.json"), {"kind": "x"})
    E.write_if_changed(E.local_path(web, "nfl/teams/buf.json"), {"kind": "x"})
    s3 = FakeS3()
    E.upload(dest=web, client=s3, log=lambda *_: None)
    os.remove(E.local_path(web, "nfl/teams/buf.json"))
    E.upload(dest=web, client=s3, log=lambda *_: None, refreshed=["nfl/"])
    assert s3.deletes == ["nfl/teams/buf.json"]


def test_a_fresh_ledger_cannot_overwrite_the_bucket_copy(env, creds):  # noqa: F811
    """A lost local tree starts a new, shorter ledger. Uploading it would replace
    the only record of what was published - refused, and NOTHING is put."""
    dest = _board_tree(env)
    s3 = FakeS3()
    E.upload(dest=dest, client=s3, log=lambda *_: None, tree="board")
    old_rows = pl.read_parquet(io.BytesIO(s3.objects["board/nfl/ledger.parquet"])).height
    assert old_rows >= 2
    J = env["J"]
    short = J.read_ledger(dest)[:1]
    for k in ("board/nfl/ledger.parquet", "board/nfl/ledger.csv"):
        os.remove(E.local_path(dest, k))
    J.write_ledger(dest, [], short)
    s3.puts.clear()
    with pytest.raises(E.AppendOnlyError, match="may not shrink"):
        E.upload(dest=dest, client=s3, log=lambda *_: None, tree="board")
    assert _put_keys(s3) == set()
    assert pl.read_parquet(io.BytesIO(s3.objects["board/nfl/ledger.parquet"])).height == old_rows


def test_a_rewritten_ledger_row_is_refused(env, creds):  # noqa: F811
    dest = _board_tree(env)
    s3 = FakeS3()
    E.upload(dest=dest, client=s3, log=lambda *_: None, tree="board")
    rows = env["J"].read_ledger(dest)
    rows[0]["price"] = 999.0
    df = pl.DataFrame(rows, schema=env["J"].ledger_schema(), orient="row")
    df.write_parquet(E.local_path(dest, "board/nfl/ledger.parquet"))
    with pytest.raises(E.AppendOnlyError, match="row 0 differs"):
        E.upload(dest=dest, client=s3, log=lambda *_: None, tree="board")


def test_an_appended_ledger_is_approved_and_an_unreadable_bucket_is_not(env, creds):  # noqa: F811
    dest = _board_tree(env)
    s3 = FakeS3()
    E.upload(dest=dest, client=s3, log=lambda *_: None, tree="board")
    J = env["J"]
    old = J.read_ledger(dest)
    extra = dict(old[0], event="void", void_reason=B.VOID_MARKET_PULLED, event_at=B.iso(T0 + 30 * H))
    J.write_ledger(dest, old, [extra])
    r = E.upload(dest=dest, client=s3, log=lambda *_: None, tree="board", dry_run=True)
    assert any("every existing row unchanged" in a for a in r["append_only"]), r["append_only"]

    class Broken(FakeS3):
        def get_object(self, Bucket, Key):
            if "ledger" in Key:
                raise RuntimeError("503 slow down")
            return super().get_object(Bucket, Key)
    b = Broken(objects=dict(s3.objects))
    with pytest.raises(E.AppendOnlyError, match="could not read the bucket"):
        E.upload(dest=dest, client=b, log=lambda *_: None, tree="board")
    assert _put_keys(b) == set()


def test_the_web_tree_skips_board_files_and_counts_them(env, creds, tmp_path):  # noqa: F811
    web = str(tmp_path / "web")
    E.write_if_changed(E.local_path(web, "nfl/manifest.json"), {"kind": "x"})
    E.write_if_changed(E.local_path(web, "board/nfl/2026/wk03/index.json"), {"kind": "x"})
    s3 = FakeS3()
    r = E.upload(dest=web, client=s3, log=lambda *_: None)
    assert r["board_skipped"] == 1 and _put_keys(s3) == {"nfl/manifest.json"}


def test_sync_keys_never_sees_a_ledger():
    """sync_keys' deletion walk uses local_keys WITHOUT tables, so an owned prefix
    over a ledger cannot delete it - by construction, asserted on the walk."""
    src = open(os.path.join(E.ROOT, "jobs", "export_web.py"), encoding="utf-8").read()
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "sync_keys")
    calls = [c for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
             and c.func.id == "local_keys"]
    assert calls and all(not c.keywords and len(c.args) == 1 for c in calls)


# ---- a lost tree ------------------------------------------------------------------------

def test_a_lost_tree_is_refused_before_reading_and_restore_recovers_it(env, creds, tmp_path):  # noqa: F811
    J = env["J"]
    dest = _board_tree(env)
    s3 = FakeS3()
    E.upload(dest=dest, client=s3, log=lambda *_: None, tree="board")
    assert "tree intact" in J.tree_intact(dest, s3, "b", log=lambda *_: None)
    fresh = str(tmp_path / "fresh")                # the machine lost its tree
    with pytest.raises(SystemExit, match="--restore"):
        J.tree_intact(fresh, s3, "b", log=lambda *_: None)
    assert J.restore(fresh, s3, "b", log=lambda *_: None) == 5
    assert "tree intact" in J.tree_intact(fresh, s3, "b", log=lambda *_: None)
    assert J.read_ledger(fresh) == J.read_ledger(dest)


# ================================================================== the cadence

def test_tick_reads_only_when_due_and_treats_no_lines_as_not_a_failure(env, monkeypatch):
    J = env["J"]
    logs = []
    # Monday before the window opens: nothing due, nothing written.
    out = J.tick(2026, env["dest"], now_ts=T0 - 2 * 86400, log=logs.append)
    assert out["due"] == [] and out["read"] == []
    # Wednesday, window open, no lines posted: due, but NoRows - not an exception.
    out = J.tick(2026, env["dest"], now_ts=T0, log=logs.append)
    assert out["due"] == ["wk03:read"] and out["no_rows"] == [3] and out["read"] == []
    # Lines post: the next tick reads.
    _first_snapshot()
    out = J.tick(2026, env["dest"], now_ts=T0 + 60, log=logs.append)
    assert out["read"] and out["read"][0]["week"] == 3
    # Five minutes later: not due (hourly this far from kickoff).
    out = J.tick(2026, env["dest"], now_ts=T0 + 360, log=logs.append)
    assert out["due"] == []
    # Inside two hours of the Thursday kickoff: every 15 minutes.
    t = K_GB - 90 * 60
    assert J.tick(2026, env["dest"], now_ts=t, log=logs.append)["due"] == ["wk03:read"]
    assert J.tick(2026, env["dest"], now_ts=t + 10 * 60, log=logs.append)["due"] == []
    assert J.tick(2026, env["dest"], now_ts=t + 15 * 60, log=logs.append)["due"] == ["wk03:read"]


def test_after_the_last_kickoff_tick_grades_hourly_until_nothing_is_live(env):
    J = env["J"]
    _first_snapshot()
    J.tick(2026, env["dest"], now_ts=T0 + 60, log=lambda *_: None)
    after = K_DET + 5 * H
    out = J.tick(2026, env["dest"], now_ts=after, log=lambda *_: None)
    assert out["due"] == ["wk03:grade"], out
    assert J.tick(2026, env["dest"], now_ts=after + 30 * 60, log=lambda *_: None)["due"] == []
    assert J.tick(2026, env["dest"], now_ts=after + H, log=lambda *_: None)["due"] == ["wk03:grade"]


def test_tick_uploads_the_board_tree_and_nothing_else(env, creds, monkeypatch):  # noqa: F811
    J = env["J"]
    _first_snapshot()
    s3 = FakeS3()
    out = J.tick(2026, env["dest"], upload=True, now_ts=T0 + 60, client=s3, log=lambda *_: None)
    assert out["upload"]["uploaded"] == 4 and out["upload"]["deleted"] == 0
    assert all(k.startswith("board/") for k in _put_keys(s3))


def test_the_cmd_runs_the_tick_from_its_own_clone_with_upload():
    cmd = open(os.path.join(E.ROOT, "run_board_tick.cmd"), encoding="utf-8").read()
    assert 'cd /d "%~dp0"' in cmd and "-m jobs.board_read --tick --upload" in cmd
    assert "PYTHONIOENCODING=utf-8" in cmd and "exit /b" in cmd and "--log" in cmd
    assert ".venv\\Scripts\\python.exe" in cmd


def test_a_tick_that_finds_another_writer_skips_and_writes_nothing(env):
    """One writer per tree. Held by a SEPARATE PROCESS, because a lock held in
    this one proves nothing about the case it exists for (a slow read overlapping
    the next scheduled tick)."""
    import subprocess
    import sys
    import time as _t
    J = env["J"]
    _first_snapshot()
    lock = J.board_lock(env["dest"]).path
    holder = subprocess.Popen([sys.executable, "-c", (
        "import sys, time; sys.path.insert(0, r'%s')\n"
        "from core.single_instance import InstanceLock\n"
        "with InstanceLock(r'%s'):\n"
        "    print('held', flush=True); time.sleep(60)\n") % (E.ROOT, lock)],
        stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        out = J.tick(2026, env["dest"], now_ts=T0 + 60, log=lambda *_: None)
        assert out == {"skipped": "already running"}
        assert E.local_keys(env["dest"], tables=True) == {}
    finally:
        holder.kill()
        holder.wait()
    _t.sleep(0.2)
    out = J.tick(2026, env["dest"], now_ts=T0 + 60, log=lambda *_: None)   # the guard can also pass
    assert out["read"] and out["read"][0]["week"] == 3
