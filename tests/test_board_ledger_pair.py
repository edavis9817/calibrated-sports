"""a-35: the two halves of f-21's attack on a-31 that did not hold.

Run: pytest -q tests/test_board_ledger_pair.py

1. THE LEDGER'S CSV AND PARQUET MAY NOT DISAGREE. `write_ledger` replaces them as
   two atomic steps; f-21 killed it between the two and the next upload shipped
   parquet 5 rows beside CSV 4, persistently. Now: the upload refuses a split pair
   with both counts, and the next read (or the next tick, read or not) re-pairs a
   CSV that trails its parquet. f-21's plant is reproduced here through the real
   job and shown caught.
2. THE FOUR-WAY PARTITION IS ENCODED. An index is valid only beside the read it
   names as latest, and only if its counts are that read's lean rows by status.
   f-21's three plants - all-zero counts, +1000, a graded lean row removed - were
   all accepted by the contract before this; each is shown refused here, and the
   untampered pair shown accepted, so the check discriminates.
"""
import io
import json
import os

import polars as pl
import pytest

from core import board as B
from jobs import export_web as E
from jobs import source_registry as R
from tests.test_board import H, K_DET, K_GB, T0, env, read, snapshot, stats  # noqa: F401
from tests.test_board_contract import _scenario_first_two_reads
from tests.test_export_web import FakeS3, creds  # noqa: F401

QUIET = dict(log=lambda *_: None)
LEDGER = ("board/nfl/ledger.parquet", "board/nfl/ledger.csv")


@pytest.fixture(autouse=True)
def _fresh_runtime_ledger():
    yield
    R._READS.pop("nfl", None)


def _rows(key, data):
    buf = io.BytesIO(data)
    return (pl.read_parquet(buf) if key.endswith(".parquet")
            else pl.read_csv(buf, infer_schema_length=0)).height


def local_counts(dest):
    return tuple(_rows(k, open(E.local_path(dest, k), "rb").read()) for k in LEDGER)


def bucket_counts(s3):
    return tuple(_rows(k, s3.objects[k]) for k in LEDGER)


def three_reads(env):
    """a-31's first two reads, then Thursday's game in progress (f-21's setup)."""
    _scenario_first_two_reads(env)
    snapshot(K_GB + H, "ev-2026_03_ATL_GB",
             [("player_receptions", "Drake London", 2.5, -300, 240, None)])
    read(env, K_GB + 1.5 * H)


def crash_between_ledger_files(env, monkeypatch):
    """f-21's plant: the grading read's CSV write dies after the parquet landed."""
    stats("2026_03_ATL_GB", 3, {"00-DL": 8}, {"pfr-dl": 50, "pfr-br": 0})
    real = pl.DataFrame.write_csv

    def boom(self, *a, **k):
        raise OSError("a-35: planted crash between the parquet and the csv write")
    monkeypatch.setattr(pl.DataFrame, "write_csv", boom)
    with pytest.raises(OSError, match="planted crash"):
        env["J"].run(2026, 3, env["dest"], read_ts=K_GB + 5 * H, **QUIET)
    monkeypatch.setattr(pl.DataFrame, "write_csv", real)


# ================================================================ 1. the pair

def test_f21_crash_between_the_ledger_files_is_refused_at_upload_and_healed(env, creds, monkeypatch):  # noqa: F811
    J, dest = env["J"], env["dest"]
    three_reads(env)
    s3 = FakeS3()
    E.upload(dest=dest, client=s3, tree="board", **QUIET)
    before = bucket_counts(s3)
    assert before[0] == before[1]

    crash_between_ledger_files(env, monkeypatch)
    pq, csv = local_counts(dest)
    assert pq == csv + 1, (pq, csv)              # the split f-21 planted, reproduced

    # THE UPLOAD REFUSES, naming both counts, and puts NOTHING (dry run too).
    s3.puts.clear()
    for dry in (True, False):
        with pytest.raises(E.TablePairError, match=rf"ledger\.csv {csv} rows, ledger\.parquet {pq} rows"):
            E.upload(dest=dest, client=s3, tree="board", dry_run=dry, **QUIET)
    assert s3.puts == {} and bucket_counts(s3) == before

    # The next read appends nothing (f-21: the case that did NOT heal) - and re-pairs.
    J.run(2026, 3, dest, read_ts=K_GB + 6 * H, **QUIET)
    assert local_counts(dest) == (pq, pq)
    r = E.upload(dest=dest, client=s3, tree="board", **QUIET)
    assert bucket_counts(s3) == (pq, pq)
    assert any("every existing row unchanged" in a for a in r["append_only"]), r["append_only"]
    assert r["table_pairs"] == [f"board/nfl/ledger: 2 formats, {pq} rows each"]
    # the healed CSV is byte-for-byte what an uncrashed write produces
    assert open(J.ledger_csv_path(dest), "rb").read() == \
        pl.read_parquet(J.ledger_path(dest)).write_csv().encode()


def test_a_tick_with_no_read_due_re_pairs_before_it_uploads(env, creds, monkeypatch):  # noqa: F811
    """Without this a split pair would be refused every five minutes until some
    read happened to append."""
    J, dest = env["J"], env["dest"]
    three_reads(env)
    s3 = FakeS3()
    E.upload(dest=dest, client=s3, tree="board", **QUIET)
    crash_between_ledger_files(env, monkeypatch)
    pq, csv = local_counts(dest)
    assert pq == csv + 1
    monkeypatch.setattr(J, "due_reads", lambda *a, **k: [])
    out = J.tick(2026, dest, upload=True, now_ts=K_GB + 6 * H, client=s3, **QUIET)
    assert out["read"] == [] and "re-paired" in out["ledger_pair"]
    assert local_counts(dest) == (pq, pq) and bucket_counts(s3) == (pq, pq)


def test_the_pair_check_passes_a_paired_tree_and_names_it(env, creds):  # noqa: F811
    three_reads(env)
    s3 = FakeS3()
    r = E.upload(dest=env["dest"], client=s3, tree="board", **QUIET)
    n = local_counts(env["dest"])[0]
    assert r["table_pairs"] == [f"board/nfl/ledger: 2 formats, {n} rows each"] and n >= 2
    assert env["J"].pair_ledger(env["dest"]) == f"ledger paired: parquet {n} rows, csv {n} rows"


def test_pair_ledger_heals_only_a_trailing_prefix_and_refuses_every_other_split(env):
    J, dest = env["J"], env["dest"]
    three_reads(env)
    pq = pl.read_parquet(J.ledger_path(dest))
    csv_p = J.ledger_csv_path(dest)
    good = open(csv_p, "rb").read()

    # CSV AHEAD of its parquet: not a crash this job leaves - refused, not shrunk
    pq.head(pq.height - 1).write_parquet(J.ledger_path(dest))
    with pytest.raises(E.ContractError, match="ahead of its parquet"):
        J.pair_ledger(dest)
    assert open(csv_p, "rb").read() == good
    pq.write_parquet(J.ledger_path(dest))

    # a trailing CSV whose existing row DISAGREES: refused, not overwritten
    bad = pq.head(pq.height - 1).with_columns(pl.lit("tampered").alias("band"))
    bad.write_csv(csv_p)
    tampered = open(csv_p, "rb").read()
    with pytest.raises(E.ContractError, match="not the leading rows"):
        J.pair_ledger(dest)
    assert open(csv_p, "rb").read() == tampered

    # a CSV with no parquet: refused
    os.replace(J.ledger_path(dest), J.ledger_path(dest) + ".away")
    with pytest.raises(E.ContractError, match="no ledger.parquet"):
        J.pair_ledger(dest)
    os.replace(J.ledger_path(dest) + ".away", J.ledger_path(dest))

    # a missing CSV is a trailing one (zero rows): rewritten
    os.remove(csv_p)
    assert "re-paired" in J.pair_ledger(dest, log=lambda *_: None)
    assert open(csv_p, "rb").read() == good


def test_the_upload_pair_check_is_over_the_whole_tree_not_the_changed_keys(env, creds):  # noqa: F811
    """In f-21's crash only the parquet CHANGED; a check over changed keys alone
    would never have read the stale CSV."""
    J, dest = env["J"], env["dest"]
    three_reads(env)
    s3 = FakeS3()
    E.upload(dest=dest, client=s3, tree="board", **QUIET)
    pq = pl.read_parquet(J.ledger_path(dest))
    # the CSV is untouched (so it is not a changed key); only the parquet grows
    extra = pq.tail(1).with_columns(pl.lit("void").alias("event"),
                                    pl.lit(B.VOID_MARKET_PULLED).alias("void_reason"))
    pl.concat([pq, extra]).write_parquet(J.ledger_path(dest))
    with pytest.raises(E.TablePairError, match=f"ledger.parquet {pq.height + 1} rows"):
        E.upload(dest=dest, client=s3, tree="board", **QUIET)


# ============================================================ 2. the partition

def _latest(env):
    J = env["J"]
    wd = J.week_dir(env["dest"], 2026, 3)
    idx = json.load(open(os.path.join(wd, "index.json")))
    rk, ik = J.read_key(2026, 3, idx["latest"]), J.index_key(2026, 3)
    doc = json.load(open(os.path.join(wd, J.read_name(idx["latest"]))))
    return ik, idx, rk, doc


def _graded_week(env):
    three_reads(env)
    stats("2026_03_ATL_GB", 3, {"00-DL": 8}, {"pfr-dl": 50, "pfr-br": 0})
    read(env, K_GB + 5 * H)
    ik, idx, rk, doc = _latest(env)
    assert idx["leans"]["graded"] >= 1, idx["leans"]    # the plant needs a graded lean
    return ik, idx, rk, doc


def test_the_real_index_and_read_reconcile(env):
    ik, idx, rk, doc = _graded_week(env)
    E.validate_contract({ik: idx, rk: doc})
    assert "index and latest read" in B.index_reconciles(idx, doc)
    assert B.row_partition(doc["rows"]) == idx["leans"]


@pytest.mark.parametrize("plant", ["all_zero", "plus_1000", "one_off"])
def test_an_index_whose_counts_are_not_its_reads_rows_is_refused(env, plant):
    ik, idx, rk, doc = _graded_week(env)
    bad = json.loads(json.dumps(idx))
    if plant == "all_zero":
        bad["leans"] = {s: 0 for s in bad["leans"]}
    elif plant == "plus_1000":
        bad["leans"] = {s: v + 1000 for s, v in bad["leans"].items()}
    else:
        bad["leans"]["graded"] += 1
    # the per-file schema alone still accepts it - which is why this check exists
    assert not list(E.contract_validators()["board_index"].iter_errors(bad))
    with pytest.raises(E.ContractError, match="do not reconcile"):
        E.validate_contract({ik: bad, rk: doc})


def test_a_read_with_a_graded_lean_row_removed_is_refused(env):
    ik, idx, rk, doc = _graded_week(env)
    graded = [r for r in doc["rows"] if r.get("lean") and r["status"] in B.SETTLED
              and r["status"] != B.VOID]
    assert graded
    cut = dict(doc, rows=[r for r in doc["rows"] if r["row_id"] != graded[0]["row_id"]])
    assert cut["rows"]                            # still a schema-valid read
    assert not list(E.contract_validators()["board_read"].iter_errors(cut))
    with pytest.raises(E.ContractError, match=r"graded index \d+ / read \d+"):
        E.validate_contract({ik: idx, rk: cut})


def test_an_index_validated_without_its_latest_read_is_refused(env):
    ik, idx, rk, doc = _graded_week(env)
    with pytest.raises(E.ContractError, match="not in this batch"):
        E.validate_contract({ik: idx})
    # an OLDER read in the batch is not the one it indexes
    older = sorted(idx["reads"])[0]
    ok = env["J"].read_key(2026, 3, older)
    odoc = json.load(open(E.local_path(env["dest"], ok)))
    with pytest.raises(E.ContractError, match="not in this batch"):
        E.validate_contract({ik: idx, ok: odoc})


def test_a_duplicated_row_and_a_mismatched_week_are_refused(env):
    ik, idx, rk, doc = _graded_week(env)
    unleaned = [r for r in doc["rows"] if not r.get("lean")]
    row = (unleaned or doc["rows"])[0]
    with pytest.raises(E.ContractError, match="more than once"):
        E.validate_contract({ik: idx, rk: dict(doc, rows=doc["rows"] + [row])})
    with pytest.raises(E.ContractError, match="index week"):
        E.validate_contract({ik: dict(idx, week=4), rk: doc})


def test_the_job_refuses_to_write_an_index_its_read_does_not_back(env, monkeypatch):
    """The producer path: `run` validates the pair before any write, so a job
    whose counting drifted from its rows writes nothing."""
    J, dest = env["J"], env["dest"]
    three_reads(env)
    before = sorted(E.local_keys(dest, tables=True))
    real = B.lean_states

    def off_by_one(ledger, now_ts):
        s = real(ledger, now_ts)
        k = next(iter(s))
        return {**s, k: B.S_VOID if s[k] != B.S_VOID else B.S_GRADED}
    monkeypatch.setattr(B, "lean_states", off_by_one)
    # a-34's ledger-vs-rows guard catches this first ...
    with pytest.raises(AssertionError, match="on the ledger"):
        J.run(2026, 3, dest, read_ts=K_GB + 6 * H, **QUIET)
    # ... and with it switched off, the contract refuses on its own: two layers.
    monkeypatch.setattr(B, "leans_on_board", lambda *a, **k: "skipped")
    with pytest.raises(E.ContractError, match="do not reconcile"):
        J.run(2026, 3, dest, read_ts=K_GB + 6 * H, **QUIET)
    assert sorted(E.local_keys(dest, tables=True)) == before


# ================================================ 3. f-21's surviving gate bypass

def test_an_unmapped_read_on_a_second_connection_with_joined_sql_is_refused(env, monkeypatch):
    """f-21's plant, verbatim in shape: a second connection, SQL assembled by
    str.join so the static scan cannot name the table. It passed the gate and the
    file was written; now the runtime ledger sees it and nothing is written."""
    import sqlite3

    import config
    J = env["J"]
    real_connect = sqlite3.connect
    snapshot(T0 - H, "ev-2026_03_NYJ_DET",
             [("player_receptions", "Amon-Ra St. Brown", 7.5, -105, -115, None)])
    real = J.week_games

    def planted(con, season, week):
        sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True).execute(
            " ".join(["SELECT COUNT(*)", "FROM", "source_health"])).fetchall()
        return real(con, season, week)
    monkeypatch.setattr(J, "week_games", planted)
    with pytest.raises(R.SourceRegistryError, match="source_health"):
        J.run(2026, 3, env["dest"], read_ts=T0, **QUIET)
    assert E.local_keys(env["dest"], tables=True) == {}
    assert sqlite3.connect is real_connect            # restored after the raise

    # discriminating: the same run without the plant writes, and restores connect
    monkeypatch.setattr(J, "week_games", real)
    J.run(2026, 3, env["dest"], read_ts=T0, **QUIET)
    assert E.local_keys(env["dest"], tables=True)
    assert sqlite3.connect is real_connect
