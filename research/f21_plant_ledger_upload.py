"""f-21: plant the failures a-31's Board uploader claims to survive, against REAL boto3.

a-31 showed its guards against `tests.test_export_web.FakeS3`, a double whose
`get_object` raises KeyError for a missing key. This module runs the same uploader
against moto's in-process S3 (a scratch bucket that exists only in this process), so
boto3's real ClientError(NoSuchKey) shape, real Content-Type storage and real
put/get round trips are what the guards meet. Nothing here can reach R2: no endpoint,
fake credentials, and `mock_aws` intercepts every call.

It is NOT a test of a-31's intent. Each test RECORDS what happened into
$F21_OUT/<test>.json and asserts only what was observed, so a reader sees the outcome
whichever way it went. Run it from a checkout of a-31 (5675347):

    cp research/f21_plant_ledger_upload.py <a31>/tests/test_f21_plant.py
    cp research/f21_board_partition.py <a31>/research/
    cd <a31> && F21_OUT=<dir> python -m pytest -q tests/test_f21_plant.py -p no:cacheprovider

The scenario (fixture store, two DET/GB games, a-26's own St. Brown line move) comes
from a-31's tests, so the Board tree here is written by the real job.
"""
import io
import json
import os
import subprocess
import sys

import boto3
import polars as pl
import pytest
from moto import mock_aws

import config
from jobs import export_web as E
from jobs import source_registry as R
from tests.test_board import H, K_DET, K_GB, T0, env, read, snapshot, stats  # noqa: F401
from tests.test_board_contract import _scenario_first_two_reads

BUCKET = "f21-scratch"
LEDGER = ("board/nfl/ledger.parquet", "board/nfl/ledger.csv")
QUIET = dict(log=lambda *_: None)


@pytest.fixture(autouse=True)
def _fresh_runtime_ledger():
    yield
    R._READS.pop("nfl", None)


@pytest.fixture
def s3(monkeypatch):
    monkeypatch.setattr(config, "WEB_R2_ACCESS_KEY_ID", "f21-fake")
    monkeypatch.setattr(config, "WEB_R2_SECRET_ACCESS_KEY", "f21-fake")
    monkeypatch.setattr(config, "WEB_R2_BUCKET", BUCKET)
    for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(k, "f21-fake")
    with mock_aws():
        c = boto3.client("s3", region_name="us-east-1")
        c.create_bucket(Bucket=BUCKET)
        yield c


def record(name, obj):
    out = os.environ.get("F21_OUT")
    if out:
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, f"{name}.json"), "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=1, default=str)


def bucket(c):
    """{key: bytes} of every object in the scratch bucket."""
    keys = [o["Key"] for p in c.get_paginator("list_objects_v2").paginate(Bucket=BUCKET)
            for o in p.get("Contents", ())]
    return {k: c.get_object(Bucket=BUCKET, Key=k)["Body"].read() for k in keys}


def rows_of(key, data):
    buf = io.BytesIO(data)
    return (pl.read_parquet(buf) if key.endswith(".parquet")
            else pl.read_csv(buf, infer_schema_length=0)).height


def ledger_counts(objs):
    return {k: (rows_of(k, objs[k]) if k in objs else None) for k in LEDGER}


def local_ledger_counts(dest):
    return {k: (rows_of(k, open(E.local_path(dest, k), "rb").read())
                if os.path.exists(E.local_path(dest, k)) else None) for k in LEDGER}


def four_reads(env):
    """a-31's first two reads, then Thursday's game in progress and its stats landing."""
    _scenario_first_two_reads(env)
    snapshot(K_GB + H, "ev-2026_03_ATL_GB", [("player_receptions", "Drake London", 2.5, -300, 240, None)])
    read(env, K_GB + 1.5 * H)


# ======================================================== 1. deletion by absence


def test_1_ledger_survives_absence_against_real_boto3(env, s3, tmp_path):
    J = env["J"]
    dest = env["dest"]
    _scenario_first_two_reads(env)
    first = E.upload(dest=dest, client=s3, tree="board", **QUIET)
    before = bucket(s3)
    heads = {k: s3.head_object(Bucket=BUCKET, Key=k) for k in before if k.startswith("board/")}
    types = {k: h["ContentType"] for k, h in heads.items()}
    cache = {k: h.get("CacheControl") for k, h in heads.items()}
    obs = {"first_upload": {k: first[k] for k in ("uploaded", "deleted", "append_only")},
           "content_types": types, "cache_control": cache,
           "ledger_rows_in_bucket": ledger_counts(before)}

    # (a) the Board's own tree, ledger gone locally, uploaded again
    for k in LEDGER:
        os.remove(E.local_path(dest, k))
    r = E.upload(dest=dest, client=s3, tree="board", **QUIET)
    after_a = bucket(s3)
    obs["a_board_tree_ledger_missing_locally"] = {
        "deleted": r["deleted"], "removed_withheld": r["removed_withheld"],
        "ledger_bytes_identical": all(after_a.get(k) == before[k] for k in LEDGER)}

    # (b) the scheduled entry point on that tree: refused before reading?
    try:
        out = J.tick(2026, dest, upload=True, now_ts=T0 + 22 * H, client=s3, **QUIET)
        obs["b_tick_on_tree_missing_ledger"] = {"refused": False, "out": out}
    except SystemExit as e:
        obs["b_tick_on_tree_missing_ledger"] = {"refused": True, "message": str(e)[:300]}
    after_b = bucket(s3)
    obs["b_ledger_bytes_identical"] = all(after_b.get(k) == before[k] for k in LEDGER)

    # (c) the WEB tree holding the Board's upload record (the worst record there is),
    # every declaration shape we could think of
    web = str(tmp_path / "web")
    E.write_if_changed(E.local_path(web, "nfl/manifest.json"), {"kind": "x"})
    json.dump(json.load(open(os.path.join(dest, E.STATE_FILE))),
              open(os.path.join(web, E.STATE_FILE), "w"))
    decls = {}
    for decl in (["board/"], ["board"], ["board/nfl/"], ["b"], [""], ["/"], ["./board/"],
                 ["Board/"], ["nfl/", "research/", "analytics/", "sports.json", "_state/"]):
        try:
            rr = E.upload(dest=web, client=s3, refreshed=decl, **QUIET)
            decls[" ".join(decl) or "<empty string>"] = {"refused": False, "deleted": rr["deleted"],
                                                         "removed_withheld": rr["removed_withheld"]}
        except ValueError as e:
            decls[" ".join(decl) or "<empty string>"] = {"refused": True, "why": str(e)[:160]}
    after_c = bucket(s3)
    obs["c_web_tree_with_board_record"] = decls
    obs["c_board_keys_before_after"] = [sorted(k for k in before if k.startswith("board/")),
                                        sorted(k for k in after_c if k.startswith("board/"))]
    obs["c_ledger_bytes_identical"] = all(after_c.get(k) == before[k] for k in LEDGER)
    record("1_absence", obs)

    assert first["deleted"] == 0
    assert obs["a_board_tree_ledger_missing_locally"]["ledger_bytes_identical"]
    assert obs["c_ledger_bytes_identical"]
    assert obs["c_board_keys_before_after"][0] == obs["c_board_keys_before_after"][1]


def test_1_first_upload_is_classified_by_real_nosuchkey_and_access_denied_refuses(env, s3):
    """_absent() was written against ClientError by reading; here boto3 raises it."""
    dest = env["dest"]
    _scenario_first_two_reads(env)
    data = open(E.local_path(dest, LEDGER[0]), "rb").read()
    first = E.check_append_only(s3, BUCKET, LEDGER[0], data)
    try:
        s3.get_object(Bucket=BUCKET, Key=LEDGER[0])
        code = None
    except Exception as e:  # noqa: BLE001
        code = (type(e).__name__, e.response["Error"]["Code"])
    # a bucket that does not exist: a real error that is NOT absence of the key
    try:
        E.check_append_only(s3, "f21-no-such-bucket", LEDGER[0], data)
        nb = "approved"
    except E.AppendOnlyError as e:
        nb = f"refused: {str(e)[:200]}"
    except Exception as e:  # noqa: BLE001
        nb = f"raised {type(e).__name__}: {str(e)[:200]}"
    record("1_error_shapes", {"missing_key_error": code, "first_upload_statement": first,
                              "missing_bucket": nb})
    assert "first upload" in first


# ======================================================== 2. the job errors halfway


def test_2a_crash_between_parquet_and_csv_then_upload(env, s3, monkeypatch):
    """write_ledger replaces parquet, THEN csv, as two atomic steps. Kill it between them."""
    J = env["J"]
    dest = env["dest"]
    four_reads(env)
    E.upload(dest=dest, client=s3, tree="board", **QUIET)
    base = ledger_counts(bucket(s3))
    stats("2026_03_ATL_GB", 3, {"00-DL": 8}, {"pfr-dl": 50, "pfr-br": 0})
    real = pl.DataFrame.write_csv

    def boom(self, *a, **k):
        raise OSError("f21: planted crash between the parquet and the csv write")
    monkeypatch.setattr(pl.DataFrame, "write_csv", boom)
    try:
        J.run(2026, 3, dest, read_ts=K_GB + 5 * H, **QUIET)
        crashed = False
    except OSError:
        crashed = True
    monkeypatch.setattr(pl.DataFrame, "write_csv", real)
    local = local_ledger_counts(dest)
    obs = {"bucket_before": base, "crashed": crashed, "local_after_crash": local,
           "tmp_files_left": sorted(f for f in os.listdir(os.path.dirname(J.ledger_path(dest)))
                                    if f.endswith(".tmp"))}
    try:
        J.check_tree(dest, **QUIET)
        obs["check_tree"] = "passed"
    except Exception as e:  # noqa: BLE001
        obs["check_tree"] = f"refused: {type(e).__name__}: {str(e)[:200]}"
    try:
        r = E.upload(dest=dest, client=s3, tree="board", **QUIET)
        obs["upload"] = {"uploaded": r["uploaded"], "append_only": r["append_only"]}
    except Exception as e:  # noqa: BLE001
        obs["upload"] = f"refused: {type(e).__name__}: {str(e)[:200]}"
    obs["bucket_after_upload"] = ledger_counts(bucket(s3))
    # does the next ordinary read heal it?
    J.run(2026, 3, dest, read_ts=K_DET + 1 * H, **QUIET)
    obs["local_after_next_read"] = local_ledger_counts(dest)
    E.upload(dest=dest, client=s3, tree="board", **QUIET)
    obs["bucket_after_next_upload"] = ledger_counts(bucket(s3))
    record("2a_crash_between_ledger_files", obs)
    assert crashed


def test_2a_tick_crash_then_upload_does_not_run(env, s3, monkeypatch):
    """The same crash inside the scheduled entry point: does the tick still upload?"""
    J = env["J"]
    dest = env["dest"]
    four_reads(env)
    E.upload(dest=dest, client=s3, tree="board", **QUIET)
    before = bucket(s3)
    stats("2026_03_ATL_GB", 3, {"00-DL": 8}, {"pfr-dl": 50, "pfr-br": 0})

    def boom(self, *a, **k):
        raise OSError("f21: planted crash inside the tick")
    monkeypatch.setattr(pl.DataFrame, "write_csv", boom)
    try:
        out = J.tick(2026, dest, upload=True, now_ts=K_GB + 5 * H, client=s3, **QUIET)
        res = {"raised": False, "out": out}
    except Exception as e:  # noqa: BLE001
        res = {"raised": True, "error": f"{type(e).__name__}: {str(e)[:160]}"}
    after = bucket(s3)
    res["bucket_changed_keys"] = sorted(k for k in set(before) | set(after)
                                        if before.get(k) != after.get(k))
    res["local_after"] = local_ledger_counts(dest)
    res["bucket_ledger"] = ledger_counts(after)
    record("2a_tick_crash", res)


def test_2b_crash_after_ledger_before_json(env, s3, monkeypatch):
    """a-31 writes the ledger, then the read and index. Kill it between them, upload,
    and ask what a reader of the bucket can see."""
    J = env["J"]
    dest = env["dest"]
    four_reads(env)
    E.upload(dest=dest, client=s3, tree="board", **QUIET)
    stats("2026_03_ATL_GB", 3, {"00-DL": 8}, {"pfr-dl": 50, "pfr-br": 0})

    def boom(*a, **k):
        raise OSError("f21: planted crash after the ledger, before the JSON")
    real = E.sync_keys
    E.sync_keys = boom
    try:
        J.run(2026, 3, dest, read_ts=K_GB + 5 * H, **QUIET)
        crashed = False
    except OSError:
        crashed = True
    finally:
        E.sync_keys = real
    r = E.upload(dest=dest, client=s3, tree="board", **QUIET)
    objs = bucket(s3)
    idx = json.loads(objs["board/nfl/2026/wk03/index.json"])
    led = pl.read_parquet(io.BytesIO(objs[LEDGER[0]])).to_dicts()
    reads = set(idx["reads"])
    dangling = [e for e in led if e["read_at"] not in reads and e["event"] == "published"]
    terminal_after_latest = [e for e in led if e["event"] != "published"
                             and e["event_at"] > idx["latest"]]
    record("2b_crash_after_ledger", {
        "crashed": crashed, "upload_append_only": r["append_only"],
        "index_latest_in_bucket": idx["latest"], "index_leans_in_bucket": idx["leans"],
        "ledger_rows_in_bucket": len(led),
        "published_events_naming_a_read_the_bucket_lacks": len(dangling),
        "terminal_events_newer_than_the_index": len(terminal_after_latest),
        "examples": [{k: e[k] for k in ("event", "lean_id", "result", "event_at")}
                     for e in terminal_after_latest[:4]]})
    assert crashed


def test_2c_partial_upload_leaves_csv_and_parquet_disagreeing(env, s3):
    """One put fails mid-upload. The two ledger files are uploaded independently."""
    J = env["J"]
    dest = env["dest"]
    four_reads(env)
    E.upload(dest=dest, client=s3, tree="board", **QUIET)
    base = ledger_counts(bucket(s3))
    stats("2026_03_ATL_GB", 3, {"00-DL": 8}, {"pfr-dl": 50, "pfr-br": 0})
    J.run(2026, 3, dest, read_ts=K_GB + 5 * H, **QUIET)
    local = local_ledger_counts(dest)

    class Flaky:
        def __init__(self, inner, fail_key):
            self.inner, self.fail_key = inner, fail_key

        def put_object(self, **kw):
            if kw["Key"] == self.fail_key:
                raise ConnectionError(f"f21: planted put failure on {self.fail_key}")
            return self.inner.put_object(**kw)

        def __getattr__(self, name):
            return getattr(self.inner, name)

    out = {"bucket_before": base, "local": local}
    for fail in (LEDGER[1], LEDGER[0]):
        # restore the bucket's ledger to the base copy for the second arm by re-running from
        # a fresh arm is not possible without deleting; so the arms run in sequence and
        # each records what it saw
        try:
            E.upload(dest=dest, client=Flaky(s3, fail), tree="board", **QUIET)
            res = "completed"
        except Exception as e:  # noqa: BLE001
            res = f"raised {type(e).__name__}: {str(e)[:120]}"
        out[f"fail_{os.path.basename(fail)}"] = {"upload": res,
                                                 "bucket_ledger": ledger_counts(bucket(s3))}
    r = E.upload(dest=dest, client=s3, tree="board", **QUIET)
    out["retry"] = {"uploaded": r["uploaded"], "append_only": r["append_only"],
                    "bucket_ledger": ledger_counts(bucket(s3))}
    record("2c_partial_upload", out)


# ======================================================== 3. the partition on the scenario


def test_3_partition_after_a_main_line_move(env, tmp_path):
    """a-26's St. Brown case, written by a-31's job: a lean published at 7.5, the main
    line moves to 6.5, a second lean published. What does the latest read carry, and does
    the contract accept the index/read as written and as planted?"""
    J = env["J"]
    DET = "ev-2026_03_NYJ_DET"
    snapshot(T0 - H, DET, [("player_receptions", "Amon-Ra St. Brown", 7.5, -105, -115, None),
                           ("player_receptions", "Amon-Ra St. Brown", 6.5, -160, 130, None)])
    read(env, T0)
    snapshot(T0 + 20 * H, DET, [("player_receptions", "Amon-Ra St. Brown", 7.5, 150, -180, None),
                                ("player_receptions", "Amon-Ra St. Brown", 6.5, -105, -115, None)])
    read(env, T0 + 21 * H)
    res = {}
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = os.path.join(here, "research", "f21_board_partition.py")
    p = subprocess.run([sys.executable, script, "--a31", here, "--tree", env["dest"]],
                       capture_output=True, text=True)
    res["pre_kickoff"] = json.loads(p.stdout) if p.returncode == 0 else p.stderr[-800:]
    stats("2026_03_NYJ_DET", 3, {"00-SB": 7}, {"pfr-sb": 60})
    read(env, K_DET + 5 * H)
    p = subprocess.run([sys.executable, script, "--a31", here, "--tree", env["dest"]],
                       capture_output=True, text=True)
    res["graded"] = json.loads(p.stdout) if p.returncode == 0 else p.stderr[-800:]
    record("3_partition_scenario", res)
    assert p.returncode == 0
