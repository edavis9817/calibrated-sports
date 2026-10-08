"""a-62: the holes f-22 found in a-57's record, closed - each guard shown both ways.

  D1  a reconstructed week reached published.json. Three layers:
        order     the ledger's event_at is non-decreasing in file order, or no
                  file is built (a replay appended after live rows goes back);
        stamp     written_at, from the writer's wall clock, must be before
                  kickoff (catches a replay into an EMPTY ledger, which order
                  cannot see);
        door      board_read refuses --at into BOARD_EXPORT_DIR.
  D2  informative is false EXACTLY when both interval bounds are null.
  D3  statement_parts carries no clause text.
  D4  no research row restates the over-confidence clause.

Run: pytest -q tests/test_record_replay.py
"""
import json
import os

import polars as pl
import pytest

import config
from core import board as B
from core import record as R
from jobs import export_web as E
from jobs import record_export as X
from jobs import source_registry as SR
from tests.test_board import T0, H, K_GB, env, read, snapshot  # noqa: F401
from tests.test_board_chain import _SCHEMA, _bytes, _ev, _ledger
from tests.test_board_contract import _first_snapshot
from tests.test_export_web import FakeS3, creds  # noqa: F401
from tests.test_record import KICK, graded, iso, pub

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def _fresh_runtime_ledger():
    yield
    SR._READS.pop("nfl", None)


def excluded(body):
    return sorted(r["lean_id"] for r in body["excluded_not_pre_kickoff"]["rows"])


# =============================================================================
# D1, layer 1 - the ledger may not go back in time
# =============================================================================

def _live_week():
    """Week 3 as the live read writes it: publications, then grades, in time."""
    ps = [pub(f"live{i}", game=f"2026_03_L{i}_H") for i in range(4)]
    return ps + [graded(p, "cleared" if i % 2 else "missed") for i, p in enumerate(ps)]


def _replayed_week():
    """Week 2 replayed AFTER week 3 went live, with --at naming week 2's reads:
    every read_at / event_at is before week 2's kickoff - pre-kickoff by
    construction, which is all a-57's rule checked."""
    k2 = KICK - 7 * 86400
    ps = [pub(f"rep{i}", week=2, read_ts=k2 - 3600, kick=k2, game=f"2026_02_R{i}_H")
          for i in range(3)]
    return ps + [graded(p, "cleared") for p in ps]


def test_a_replayed_week_appended_to_the_live_ledger_builds_no_file():
    live = _live_week()
    ok = R.build_published(live, KICK + 90_000)
    assert ok["n_published"] == 4 and "event_at non-decreasing" in ok["excluded_not_pre_kickoff"]["order"]
    # a-57's rule on the replay's own rows: every one pre-kickoff - this is the hole
    assert all(R.pre_kickoff(e) is None for e in _replayed_week() if e["event"] == "published")
    with pytest.raises(R.RecordError, match="goes back in time"):
        R.build_published(live + _replayed_week(), KICK + 90_000)


def test_the_order_check_is_in_time_not_in_text():
    """Two spellings of one instant compare equal; a later instant written with
    an offset is still later."""
    a, b = pub("a"), pub("b")
    b["event_at"] = b["read_at"] = "2026-09-21T13:33:20+00:00"     # == KICK, after a's
    a["event_at"] = a["read_at"] = iso(KICK - 3600)
    assert "2 rows" in R.ledger_order([a, b])
    X
    assert "2 rows" in R.ledger_order([a, b])
    b["event_at"] = "2026-09-21T08:00:00-04:00"                     # 12:00Z: before a's 12:33:20Z
    with pytest.raises(R.RecordError, match="back in time"):
        R.ledger_order([a, b])


def test_an_unparseable_event_at_refuses_the_file_rather_than_ordering_around_it():
    a = pub("a")
    a["event_at"] = None
    with pytest.raises(R.RecordError, match="not a time"):
        R.build_published([a], KICK)


# =============================================================================
# D1, layer 2 - written_at
# =============================================================================

def _stamped(e, at):
    return dict(e, written_at=iso(at))


def test_written_at_after_kickoff_excludes_a_lean_whose_read_and_event_are_before():
    early = _stamped(pub("early"), KICK - 60)
    late = _stamped(pub("late"), KICK + 60)                 # read pre-kickoff, WRITTEN after
    body = R.build_published([early, late], KICK + 90_000)
    assert excluded(body) == ["late"] and body["n_published"] == 1
    reason = body["excluded_not_pre_kickoff"]["rows"][0]["reason"]
    assert reason.startswith("written_at") and "not before kickoff" in reason


def test_a_replay_into_an_EMPTY_ledger_is_caught_by_the_stamp_and_not_by_the_order():
    """What layer 1 cannot see: nothing precedes the replay, so it is in order.
    Its rows were written after their kickoffs, and the stamp says so."""
    rows = [_stamped(e, KICK + 30 * 86400) for e in _replayed_week()]
    assert "6 rows" in R.ledger_order(rows)                 # in order - layer 1 passes it
    body = R.build_published(rows, KICK + 31 * 86400)
    assert body["n_published"] == 0 and len(excluded(body)) == 3


def test_unstamped_rows_may_only_lead_the_ledger():
    old = pub("old")
    new = _stamped(pub("new", read_ts=KICK - 1800), KICK - 1700)
    assert "1 unstamped leading row(s), 1 stamped" in R.ledger_order([old, new])
    after = pub("after", read_ts=KICK - 1000)                # a null AFTER a stamp
    with pytest.raises(R.RecordError, match="no written_at after row 1"):
        R.ledger_order([old, new, after])


def test_written_at_may_not_go_back_in_time_either():
    a = _stamped(pub("a", read_ts=KICK - 3000), KICK - 2000)
    b = _stamped(pub("b", read_ts=KICK - 2900), KICK - 2500)
    with pytest.raises(R.RecordError, match="written_at .* before row 0"):
        R.ledger_order([a, b])


def test_the_writer_stamps_its_own_clock_over_whatever_the_caller_carried(env, monkeypatch):
    J, dest = env["J"], env["dest"]
    monkeypatch.setattr(J, "wall_clock", lambda: KICK - 5000)
    forged = dict(_ev(0), written_at="2000-01-01T00:00:00Z")
    assert J.write_ledger(dest, [], [forged], log=lambda *_: None) == 1
    got = J.read_ledger(dest)
    assert [r["written_at"] for r in got] == [iso(KICK - 5000)]
    # ...and the next append stamps ITS rows and leaves the first one's alone
    monkeypatch.setattr(J, "wall_clock", lambda: KICK - 4000)
    assert J.write_ledger(dest, got, [_ev(1)], log=lambda *_: None) == 1
    assert [r["written_at"] for r in J.read_ledger(dest)] == [iso(KICK - 5000), iso(KICK - 4000)]
    assert B.verify_chain(J.read_ledger(dest)).holds


def test_a_read_run_as_a_replay_publishes_nothing_and_the_same_read_run_live_does(env, monkeypatch):
    """End to end through the job: the fixture's reads are at T0 (Sept 2026).
    Run with the wall clock at T0 they are live reads and the record publishes
    their leans; run with the wall clock after kickoff - which is what a replay
    IS - the identical rows are written, and the record publishes none of them."""
    J = env["J"]
    counts = {}
    for label, clock in (("live", lambda: T0 + 60), ("replay", lambda: K_GB + 86400 * 7)):
        dest = os.path.join(env["dest"], label)
        env_l = dict(env, dest=dest)
        monkeypatch.setattr(J, "wall_clock", clock)
        _first_snapshot()
        read(env_l, T0)
        rows = J.read_ledger(dest)
        body = R.build_published(rows, T0 + 3600)
        counts[label] = (len(rows), body["n_published"], body["excluded_not_pre_kickoff"]["n"])
        SR._READS.pop("nfl", None)
    n_rows, n_pub_live, n_ex_live = counts["live"]
    assert n_rows > 0 and n_pub_live > 0 and n_ex_live == 0, counts
    assert counts["replay"] == (n_rows, 0, n_pub_live), counts


def test_the_index_head_is_the_head_of_the_stamped_file(env, monkeypatch):
    """A stamped row hashes under the v2 tag, so a head computed before the
    stamp would name a hash the file does not hold."""
    J = env["J"]
    monkeypatch.setattr(J, "wall_clock", lambda: T0 + 60)
    _first_snapshot()
    _rows, idx = read(env, T0)
    pq = pl.read_parquet(J.ledger_path(env["dest"]))
    assert pq.height > 0 and pq["written_at"].null_count() == 0
    assert idx["ledger_head"]["row_hash"] == pq["row_hash"][-1]


# ---- the chain: a stamp is covered, and no hash written before a-62 moves

def test_an_unstamped_row_hashes_exactly_as_a48_hashed_it():
    import hashlib
    e = _ev(0)
    cells = [B.canon_value(e.get(c), B.LEDGER_DTYPES[c]) for c in B.LEDGER_COLUMNS]
    a48 = hashlib.sha256(json.dumps([B.CHAIN_TAG, *cells, B.CHAIN_GENESIS], ensure_ascii=False,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()
    assert B.row_hash(e, B.CHAIN_GENESIS) == a48
    assert B.row_hash(dict(e, written_at=None), B.CHAIN_GENESIS) == a48
    assert B.row_hash(dict(e, written_at=""), B.CHAIN_GENESIS) == a48      # CSV's null


def test_a_stamp_is_in_the_hash_so_editing_it_breaks_the_chain():
    rows = B.chain([dict(_ev(i), written_at="2026-09-26T19:20:00Z") for i in range(3)])
    assert B.verify_chain(rows).holds
    rows[1] = dict(rows[1], written_at="2026-09-26T19:00:00Z")
    rep = B.verify_chain(rows)
    assert not rep.holds and rep.first_break == 1
    rows = B.chain([dict(_ev(i), written_at="2026-09-26T19:20:00Z") for i in range(3)])
    rows[2] = dict(rows[2], written_at=None)                  # a stamp REMOVED is an edit too
    assert B.verify_chain(rows).first_break == 2


def test_the_contract_names_the_stamp_and_its_tag():
    hc = E.TABLES["board_ledger"]["hash_chain"]
    assert hc["stamped"] == {"tag": B.CHAIN_TAG_STAMPED, "column": B.STAMP_COLUMN}
    cols = list(E.TABLES["board_ledger"]["columns"])
    assert cols == list(B.ledger_file_columns())
    assert cols.index("written_at") == len(B.LEDGER_COLUMNS)


# ---- the bucket: the stamp column arrives once, null on what it already held

@pytest.mark.parametrize("key", ["board/nfl/ledger.parquet", "board/nfl/ledger.csv"])
def test_the_bucket_accepts_the_stamp_column_null_on_old_rows_and_nothing_else(key):
    rows = _ledger(4)
    chained27 = pl.DataFrame([{c: r.get(c) for c in B.ledger_file_columns() if c != "written_at"}
                              for r in rows])
    s3 = FakeS3({key: _bytes(chained27, key)})
    new = B.extend_chain(rows, [dict(_ev(4), written_at="2026-09-30T12:00:00Z")])
    full = pl.DataFrame([{c: r.get(c) for c in B.ledger_file_columns()} for r in new],
                        schema=_SCHEMA)
    ok = E.check_append_only(s3, "b", key, _bytes(full, key))
    assert "written_at column added, null on all 4 existing row(s)" in ok and "4 -> 5" in ok
    # a stamp appearing on a row the bucket already held is a rewrite
    forged = B.chain([dict(r, written_at="2026-09-01T00:00:00Z") if i == 0 else r
                      for i, r in enumerate(new)])
    bad = pl.DataFrame([{c: r.get(c) for c in B.ledger_file_columns()} for r in forged],
                       schema=_SCHEMA)
    with pytest.raises(E.AppendOnlyError):
        E.check_append_only(s3, "b", key, _bytes(bad, key))
    # the unchained 25-column bucket copy widens to all three columns at once
    legacy = chained27.select(list(B.LEDGER_COLUMNS))
    ok = E.check_append_only(FakeS3({key: _bytes(legacy, key)}), "b", key, _bytes(full, key))
    assert "hash chain added" in ok and "written_at column added" in ok


# =============================================================================
# D1, layer 3 - the door
# =============================================================================

def test_board_read_refuses_a_replay_into_the_live_tree(tmp_path, monkeypatch):
    from jobs import board_read as J
    live = tmp_path / "board_export"
    live.mkdir()
    monkeypatch.setattr(config, "BOARD_EXPORT_DIR", str(live))
    at = "2026-09-20T15:00:00Z"
    for argv in (["--season", "2026", "--week", "2", "--dest", str(live), "--at", at],
                 ["--season", "2026", "--week", "2", "--dest", str(live / "board"), "--at", at],
                 ["--tick", "--at", at],                       # --tick defaults to BOARD_EXPORT_DIR
                 ["--tick", "--dest", str(live), "--at", at]):
        with pytest.raises(SystemExit, match="replay"):
            J.main(argv)
    # the neighbouring cases this guard lets through: a scratch tree, and a live read
    assert J.refuse_replay_into_live(str(tmp_path / "scratch")) is None
    assert J.refuse_replay_into_live(str(tmp_path / "board_export_x")) is None


# =============================================================================
# D2 - no bounds unless informative, in both directions
# =============================================================================

def _weeks(n):
    ev = []
    for w in range(3, 3 + n):
        for i in range(8):
            k = KICK + w * 604800
            p = pub(f"w{w}-{i}", week=w, kick=k, read_ts=k - 3600, game=f"2026_{w:02d}_G{i}_H")
            ev += [p, graded(p, "cleared" if (i + w) % 3 else "missed")]
    ev.sort(key=lambda e: e["event_at"])
    return ev


@pytest.mark.parametrize("n", [0, 1, 2, 3, 4])
def test_informative_is_false_exactly_when_both_bounds_are_null(n):
    ev = _weeks(n) if n else [pub("u")]
    iv = R.build_published(ev, KICK + 20 * 604800)["record"]["interval"]
    assert iv["n_blocks"] == n
    nulls = iv["hit_rate"] is None and iv["margin_pp"] is None
    assert (iv["informative"] is False) == nulls           # both directions
    assert iv["informative"] == (n >= R.INFORMATIVE_MIN_BLOCKS)
    if not nulls:
        assert iv["why"] is None and len(iv["hit_rate"]) == 2 and len(iv["margin_pp"]) == 2


def test_the_contract_refuses_bounds_beside_informative_false_and_nulls_beside_true():
    ev = _weeks(3)
    body = R.build_published(ev, KICK + 20 * 604800,
                             source={"table": "board_ledger", "key": "board/nfl/ledger.parquet",
                                     "rows": len(ev), "sha256": "0" * 64})
    doc = X.envelope("record.published", "nfl", KICK, body)
    E.validate_contract({X.KEYS["published"]: doc})
    iv = doc["record"]["interval"]
    two_week_pair = dict(iv, informative=False, why="2 graded weeks")      # f-22's D2 file
    with pytest.raises(E.ContractError):
        E.validate_contract({X.KEYS["published"]: {**doc, "record": {**doc["record"], "interval": two_week_pair}}})
    blank_but_true = dict(iv, hit_rate=None, margin_pp=None)
    with pytest.raises(E.ContractError):
        E.validate_contract({X.KEYS["published"]: {**doc, "record": {**doc["record"], "interval": blank_but_true}}})


# =============================================================================
# D3 / D4 - the c-24 sentence cannot be split
# =============================================================================

def _backtest():
    doc = open(os.path.join(ROOT, "docs", "findings", "ranking-versus-calibration.md"),
               encoding="utf-8").read()
    return R.build_backtest(doc, {"doc": "d"})


def _strings(x, path=()):
    if isinstance(x, str):
        yield path, x
    elif isinstance(x, dict):
        for k, v in x.items():
            yield from _strings(v, path + (k,))
    elif isinstance(x, list):
        for i, v in enumerate(x):
            yield from _strings(v, path + (i,))


def test_no_field_but_statement_carries_clause_text():
    b = _backtest()
    first = b["statement"].split(": ")[0]           # "Against the book close, most of ... over-confidence"
    assert "over-confidence" in first
    holders = [p for p, s in _strings(b) if first in s]
    assert holders == [("statement",)], holders
    # the check discriminates: the a-57 shape (a text per part) is caught
    old = dict(b, statement_parts=[dict(p, text=first + ".") for p in b["statement_parts"]])
    assert [p for p, s in _strings(old) if first in s] != [("statement",)]
    with pytest.raises(E.ContractError):
        E.validate_contract({X.KEYS["backtest"]: X.envelope("record.backtest", None, KICK, old)})


def test_a_research_row_that_restates_the_first_clause_is_refused():
    from tests.test_record import DECL, _docs
    docs = _docs()
    good = json.loads(json.dumps(DECL))["rows"]
    R.build_research(docs, good)                                           # passes
    for field in ("note", "power", "question"):
        bad = json.loads(json.dumps(good))
        bad[0][field] = "Against the book close most of its loss is Over-Confidence."
        with pytest.raises(R.RecordError, match="first clause of the backtest statement"):
            R.build_research(docs, bad)


def test_the_real_register_restates_no_clause_and_points_nowhere_it_cannot():
    rows = json.load(open(os.path.join(ROOT, "docs", "record", "research-verdicts.json"),
                          encoding="utf-8"))["rows"]
    for d in rows:
        for field in ("question", "power", "note"):
            assert R.split_clause(d.get(field) or "") is None, (d["id"], field)
    c24 = next(d for d in rows if d["id"] == "C24")
    assert "below." not in c24["power"] and "entirely below zero" in c24["power"]
