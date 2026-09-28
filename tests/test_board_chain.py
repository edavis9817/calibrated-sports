"""a-48: the Board ledger's hash chain - the Receipts page's integrity claim, made true.

Run: pytest -q tests/test_board_chain.py

The page (design/NFL_Receipts.dc.html section 03) says: "Its hash includes the
previous row's hash, so editing or removing any row breaks every hash after it.
The head is published with each read - check it against yesterday's." Before
a-48 nothing on disk did that: `lean_id` hashes a lean's identity and references
no other row. These tests pin the chain that now does:

  holds       a ledger written by the job verifies, in the parquet AND the CSV,
              with the same hashes in both;
  edited      a cell changed in the middle fails that row and every row after;
  removed     a row taken out fails from that index on;
  appended    a new row leaves every earlier hash as it was, so yesterday's
              recorded head is still at its index - and a ledger cut short from
              the END is caught by that recorded head, which the chain alone
              cannot do;
  the split   every event column is covered, pinned column by column; the
              chain's own columns are the only ones outside the digest.
"""
import io
import json
import os

import polars as pl
import pytest

from core import board as B
from jobs import export_web as E
from jobs import source_registry as R
from tests.test_board import H, K_GB, T0, env, read, snapshot  # noqa: F401
from tests.test_board_contract import _scenario_first_two_reads
from tests.test_export_web import FakeS3, creds  # noqa: F401

QUIET = dict(log=lambda *_: None)


@pytest.fixture(autouse=True)
def _fresh_runtime_ledger():
    yield
    R._READS.pop("nfl", None)


def _ev(i, event="published", **kw):
    """One ledger event with every column set, as the job writes it."""
    e = {c: None for c in B.LEDGER_COLUMNS}
    e.update(event=event, lean_id=f"lean{i:04d}", claim_id=f"G{i}:P:receptions",
             row_id=f"2026-03-AAA-BBB:P{i}:receptions:4.5", season=2026, week=3,
             game_id=f"G{i}", gsis_id=f"00-{i:07d}", market="receptions", line=4.5,
             side="over", read_at="2026-09-26T19:19:46Z", kickoff_ts=1790528400.0,
             mkt_p_over=0.4424, mkt_books=3, model_p_over=0.5012, gap_pp=5.88, price=-146.0,
             band="4-6", model_version="baseline-usage-0.4+03571d4710af",
             lean_threshold_pp=4.0, event_at="2026-09-26T19:19:46Z")
    e.update(kw)
    return e


def _ledger(n=6):
    return B.chain([_ev(i) for i in range(n)])


# ================================================================ canonical cells

@pytest.mark.parametrize("x, js", [
    (1790528400.0, "1790528400"), (4.5, "4.5"), (-146.0, "-146"), (0.4424, "0.4424"),
    (-5.87, "-5.87"), (4.0, "4"), (0.0, "0"), (-0.0, "0"), (0.1 + 0.2, "0.30000000000000004"),
    (1e-7, "1e-7"), (1.5e-7, "1.5e-7"), (1e-6, "0.000001"), (123456.789, "123456.789"),
    (1e21, "1e+21"), (1e20, "100000000000000000000"), (1.23e22, "1.23e+22"),
    (-38.4, "-38.4"), (5e-324, "5e-324"), (1.7976931348623157e308, "1.7976931348623157e+308"),
])
def test_floats_hash_as_javascript_writes_them(x, js):
    """String(n) in a browser, character for character, so the site can verify
    the chain without reimplementing Python's float repr."""
    assert B.canon_float(x) == js


def test_a_non_finite_float_is_refused_not_hashed():
    for bad in (float("inf"), float("-inf")):
        with pytest.raises(ValueError, match="non-finite"):
            B.canon_float(bad)


def test_the_parquet_value_and_the_csv_text_hash_identically():
    typed = _ev(1, actual=7.0, result="cleared", event="graded")
    text = {c: (None if v is None else (B.canon_float(v) if isinstance(v, float) else str(v)))
            for c, v in typed.items()}
    text["kickoff_ts"] = "1790528400.0"          # polars writes the trailing .0; JS does not
    text["actual"] = "7.0"
    assert B.row_hash(typed, B.CHAIN_GENESIS) == B.row_hash(text, B.CHAIN_GENESIS)


# ================================================================ the split

def test_the_chain_covers_every_event_column_and_the_contract_says_the_same():
    hc = E.TABLES["board_ledger"]["hash_chain"]
    assert tuple(hc["covers"]) == B.LEDGER_COLUMNS
    assert (hc["prev"], hc["row"]) == B.CHAIN_COLUMNS == ("prev_hash", "row_hash")
    assert hc["tag"] == B.CHAIN_TAG and hc["genesis"] == B.CHAIN_GENESIS == "0" * 64
    assert list(E.TABLES["board_ledger"]["columns"]) == list(B.ledger_file_columns())
    # the fields the brief names as the ones that must be covered
    for must in ("lean_id", "line", "side", "price", "result", "actual", "void_reason",
                 "gap_pp", "mkt_p_over", "model_p_over", "event", "event_at"):
        assert must in hc["covers"]


@pytest.mark.parametrize("col", B.LEDGER_COLUMNS)
def test_editing_any_covered_column_breaks_the_hash(col):
    """Column by column, so a column the digest silently skipped would fail here."""
    led = _ledger(4)
    dtype = B.LEDGER_DTYPES[col]
    was = led[2][col]
    new = ((was or "") + "x" if dtype == "string" else
           (was or 0) + 1 if dtype == "int64" else (was or 0.0) + 0.5)
    led[2] = dict(led[2], **{col: new})
    rep = B.verify_chain(led)
    assert not rep.holds and rep.first_break == 2 and rep.broken == 2


def test_a_contract_hash_chain_that_drifts_from_the_job_is_refused(monkeypatch):
    from jobs import board_read as J
    J.ledger_schema()                                                  # agrees today
    monkeypatch.setitem(E.TABLES["board_ledger"], "hash_chain",
                        dict(E.TABLES["board_ledger"]["hash_chain"],
                             covers=list(B.LEDGER_COLUMNS[:-1])))       # event_at dropped
    with pytest.raises(E.ContractError, match="hash chain drifted"):
        J.ledger_schema()


def test_grading_appends_and_never_moves_an_earlier_hash():
    """The brief's worry - that grading would change a covered field and break
    every hash - does not arise: grading APPENDS a graded event and the
    published one is untouched. Shown on the pure path the job uses."""
    pub = _ledger(3)
    head = B.chain_head(pub)
    graded = dict({c: pub[1][c] for c in B.LEDGER_COLUMNS}, event="graded", result="cleared",
                  actual=6.0, event_at="2026-09-28T03:00:00Z")
    both = B.extend_chain(pub, [graded])
    assert [r["row_hash"] for r in both[:3]] == [r["row_hash"] for r in pub]
    assert B.verify_chain(both).holds
    assert "still row #2" in B.check_head(both, head["index"], head["row_hash"])


# ================================================================ the four cases

def test_a_chain_that_holds():
    led = _ledger(6)
    rep = B.verify_chain(led)
    assert rep.holds and rep.first_break is None and rep.broken == 0
    assert rep.head == {"index": 5, "rows": 6, "row_hash": led[-1]["row_hash"],
                        "event_at": led[-1]["event_at"]}
    assert led[0]["prev_hash"] == B.CHAIN_GENESIS
    assert all(led[i]["prev_hash"] == led[i - 1]["row_hash"] for i in range(1, 6))
    assert len({r["row_hash"] for r in led}) == 6
    assert rep.statement.startswith("chain holds: 6 rows, head #5 ")


def test_a_row_edited_in_the_middle_breaks_every_hash_after_it():
    led = _ledger(6)
    led[2] = dict(led[2], side="under")                   # the edit, hashes left as they were
    rep = B.verify_chain(led)
    assert not rep.holds and rep.first_break == 2 and rep.broken == 4     # rows 2, 3, 4, 5
    assert "not the hash of its cells" in rep.reason
    # and an editor who re-hashes to hide it is caught by the head recorded before
    head = B.chain_head(_ledger(6))
    rehashed = B.chain([{c: r[c] for c in B.LEDGER_COLUMNS} for r in led])
    assert B.verify_chain(rehashed).holds
    with pytest.raises(AssertionError, match="was recorded"):
        B.check_head(rehashed, head["index"], head["row_hash"])


def test_a_row_removed_breaks_every_hash_from_there():
    led = _ledger(6)
    del led[3]
    rep = B.verify_chain(led)
    assert not rep.holds and rep.first_break == 3 and rep.broken == 2     # old rows 4, 5
    assert "prev_hash" in rep.reason


def test_a_row_appended_keeps_the_chain_and_yesterdays_head():
    led = _ledger(6)
    head = B.chain_head(led)
    more = B.extend_chain(led, [_ev(6), _ev(7, event="void", void_reason="inactive")])
    assert B.verify_chain(more).holds and len(more) == 8
    assert [r["row_hash"] for r in more[:6]] == [r["row_hash"] for r in led]
    assert "still row #5" in B.check_head(more, head["index"], head["row_hash"])


def test_rows_cut_from_the_end_verify_and_only_the_recorded_head_catches_it():
    """A real limit of a hash chain, stated rather than implied away: drop the
    last rows and what remains is a valid chain. The published head is what a
    reader compares, and that is why it is published."""
    led = _ledger(6)
    head = B.chain_head(led)
    cut = led[:4]
    assert B.verify_chain(cut).holds
    with pytest.raises(AssertionError, match="past the end"):
        B.check_head(cut, head["index"], head["row_hash"])


def test_extending_a_broken_chain_is_refused_rather_than_laundered():
    led = _ledger(4)
    led[1] = dict(led[1], price=-110.0)
    with pytest.raises(AssertionError, match="refusing to extend a broken ledger chain"):
        B.extend_chain(led, [_ev(9)])


def test_a_partly_chained_ledger_is_refused():
    led = _ledger(3)
    led[1] = dict(led[1], row_hash=None)
    with pytest.raises(AssertionError, match="partly chained"):
        B.verify_chain(led)


def test_the_report_refuses_to_be_a_boolean():
    rep = B.verify_chain(_ledger(2))
    with pytest.raises(TypeError, match=r"\.holds"):
        bool(rep)
    with pytest.raises(TypeError):
        assert rep


# ================================================================ through the job

def _files(dest, J):
    return (pl.read_parquet(J.ledger_path(dest)),
            pl.read_csv(J.ledger_csv_path(dest), infer_schema_length=0))


def test_the_job_writes_a_chain_both_files_carry_and_the_index_publishes_its_head(env):
    J, dest = env["J"], env["dest"]
    _rows, idx = _scenario_first_two_reads(env)
    pq, csv = _files(dest, J)
    assert pq.height >= 2, "the scenario must publish leans or this test checks nothing"
    assert pq.columns == list(B.ledger_file_columns()) == csv.columns
    rep = J.verify_pair_chain(pq, csv)
    assert rep.holds and rep.pq.rows == pq.height
    assert pq["row_hash"].to_list() == csv["row_hash"].to_list()
    h = idx["ledger_head"]
    assert h == {"index": pq.height - 1, "rows": pq.height, "row_hash": pq["row_hash"][-1],
                 "event_at": pq["event_at"][-1], "as_of": idx["latest"]}
    latest = json.load(open(os.path.join(J.week_dir(dest, 2026, 3), J.read_name(idx["latest"]))))
    E.validate_contract({J.index_key(2026, 3): idx,
                         J.read_key(2026, 3, idx["latest"]): latest})
    lines = []
    assert J.check_tree(dest, log=lines.append)["tables"] == 2
    assert any("parquet and csv carry the same" in s for s in lines)


def test_verify_chain_command_holds_breaks_and_checks_a_recorded_head(env, capsys):
    J, dest = env["J"], env["dest"]
    _scenario_first_two_reads(env)
    pq, _csv = _files(dest, J)
    head = f"{pq.height - 1}:{pq['row_hash'][-1]}"
    assert J.main(["--verify-chain", "--dest", dest, "--head", head]) == 0
    out = capsys.readouterr().out
    assert "chain holds" in out and "is still row" in out and "verify-chain exit=0" in out
    assert J.main(["--verify-chain", "--dest", dest, "--head", f"{pq.height}:{'a' * 64}"]) == 1
    assert "past the end" in capsys.readouterr().out
    # edit the CSV's first row (a lean flipped) - the CSV breaks at 0, the pair disagrees
    path = J.ledger_csv_path(dest)
    text = open(path, encoding="utf-8").read().splitlines()
    side_at = text[0].split(",").index("side")
    cells = text[1].split(",")
    cells[side_at] = "under" if cells[side_at] == "over" else "over"
    text[1] = ",".join(cells)
    open(path, "w", encoding="utf-8", newline="").write("\n".join(text) + "\n")
    assert J.main(["--verify-chain", "--dest", dest]) == 1
    out = capsys.readouterr().out
    assert "csv:     chain BROKEN at row 0" in out and "parquet: chain holds" in out
    with pytest.raises(E.ContractError, match="csv: chain BROKEN"):
        J.check_tree(dest, **QUIET)


def test_a_later_read_keeps_every_earlier_hash(env):
    J, dest = env["J"], env["dest"]
    _scenario_first_two_reads(env)
    before = J.read_ledger(dest)
    snapshot(K_GB + H, "ev-2026_03_ATL_GB",
             [("player_receptions", "Drake London", 2.5, -300, 240, None)])
    read(env, K_GB + 1.5 * H)
    after = J.read_ledger(dest)
    assert [r["row_hash"] for r in after[:len(before)]] == [r["row_hash"] for r in before]
    assert B.verify_chain(after).holds


# ================================================================ the migration

def _unchain(dest, J):
    """Rewrite the tree's ledger as a pre-a-48 writer left it: 25 columns."""
    pq, _ = _files(dest, J)
    legacy = pq.select(list(B.LEDGER_COLUMNS))
    legacy.write_parquet(J.ledger_path(dest))
    legacy.write_csv(J.ledger_csv_path(dest))
    return legacy


def test_a_pre_a48_ledger_is_chained_on_the_next_write_with_no_cell_changed(env):
    J, dest = env["J"], env["dest"]
    _scenario_first_two_reads(env)
    chained_first = J.read_ledger(dest)
    legacy = _unchain(dest, J)
    assert J.main(["--verify-chain", "--dest", dest]) == 2                   # no chain yet
    logged = []
    assert J.write_ledger(dest, legacy.to_dicts(), [], log=logged.append) == 0  # nothing appended
    assert any("ledger chained" in s for s in logged)
    pq, csv = _files(dest, J)
    assert pq.select(list(B.LEDGER_COLUMNS)).to_dicts() == legacy.to_dicts()
    assert J.verify_pair_chain(pq, csv).holds
    assert pq["row_hash"].to_list() == [r["row_hash"] for r in chained_first]   # deterministic


def test_an_unchained_csv_beside_a_chained_parquet_is_healed_by_the_pair_check(env):
    """The migration's crash window: parquet rewritten, CSV not yet."""
    J, dest = env["J"], env["dest"]
    _scenario_first_two_reads(env)
    pq, _ = _files(dest, J)
    pq.select(list(B.LEDGER_COLUMNS)).write_csv(J.ledger_csv_path(dest))
    msg = J.pair_ledger(dest, **QUIET)
    assert "chain columns added" in msg
    pq, csv = _files(dest, J)
    assert J.verify_pair_chain(pq, csv).holds


def test_the_pair_check_refuses_two_files_with_different_hashes_at_one_count(env):
    J, dest = env["J"], env["dest"]
    _scenario_first_two_reads(env)
    pq, csv = _files(dest, J)
    csv.with_columns(pl.lit("f" * 64).alias("row_hash")).write_csv(J.ledger_csv_path(dest))
    with pytest.raises(E.ContractError, match="different row hashes"):
        J.pair_ledger(dest, **QUIET)


# ================================================================ the bucket

def _bytes(df, key):
    buf = io.BytesIO()
    (df.write_parquet if key.endswith(".parquet") else df.write_csv)(buf)
    return buf.getvalue()


@pytest.mark.parametrize("key", ["board/nfl/ledger.parquet", "board/nfl/ledger.csv"])
def test_the_bucket_accepts_the_chain_columns_once_and_nothing_else(key):
    rows = _ledger(4)
    full = pl.DataFrame([{c: r[c] for c in B.ledger_file_columns()} for r in rows])
    legacy = full.select(list(B.LEDGER_COLUMNS))
    s3 = FakeS3({key: _bytes(legacy, key)})
    ok = E.check_append_only(s3, "b", key, _bytes(full, key))
    assert "hash chain added to 4 existing row(s)" in ok and "chain holds" in ok
    # a widening that also edits a cell is refused
    edited = full.with_columns(pl.when(pl.int_range(pl.len()) == 1).then(pl.lit("under"))
                               .otherwise(pl.col("side")).alias("side"))
    edited = pl.DataFrame(B.chain(edited.select(list(B.LEDGER_COLUMNS)).to_dicts()))
    with pytest.raises(E.AppendOnlyError, match="row 1 differs"):
        E.check_append_only(s3, "b", key, _bytes(edited.select(list(B.ledger_file_columns())), key))
    # a widening whose chain does not verify is refused
    bad = full.with_columns(pl.lit("e" * 64).alias("row_hash"))
    with pytest.raises(E.AppendOnlyError, match="chain does not verify"):
        E.check_append_only(s3, "b", key, _bytes(bad, key))
    # once the bucket carries the chain, the ordinary rule applies again
    s3 = FakeS3({key: _bytes(full, key)})
    more = pl.DataFrame([{c: r[c] for c in B.ledger_file_columns()}
                         for r in B.extend_chain(rows, [_ev(4)])])
    assert "4 -> 5 rows, every existing row unchanged" == \
        E.check_append_only(s3, "b", key, _bytes(more, key)).split(": ", 1)[1]
    with pytest.raises(E.AppendOnlyError):
        E.check_append_only(s3, "b", key, _bytes(legacy, key))           # un-chaining refused
