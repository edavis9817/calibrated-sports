"""The answered ledger is append-only, and something fails when it is not (f-28).

Every test builds its own relay folder under tmp_path. The one test that reads
the real `_relay` skips, naming the folder, when it is not there (CI).
"""
import json
import os

import pytest

from relay import answered, items

CITE = {"type": "commit", "ref": "d9dd3ba", "detail": "in main"}


def _report(relay, unit, whats):
    d = relay / "reports" / "json"
    d.mkdir(parents=True, exist_ok=True)
    (d / (unit + ".json")).write_text(json.dumps({
        "unit_id": unit, "track": unit[0],
        "needs_ethan": [{"what": w, "why": "y", "irreversible": False, "recommended": "do it"} for w in whats]}),
        encoding="utf-8")


@pytest.fixture
def relay(tmp_path):
    r = tmp_path / "_relay"
    _report(r, "a-1", ["Merge a-1-first-branch to origin/main and restart the logger",
                       "Rule on the structural red items in the staleness gate"])
    _report(r, "a-2", ["Merge a-1 and restart the logger"])
    _report(r, "b-3", ["Choose the headline for the root page"])
    return r


def _known(relay):
    return {it.id: it for it in items.load(str(relay))[0]}


def _three(relay):
    path = str(relay / answered.LEDGER)
    known = _known(relay)
    for item in ("a-1#0", "a-2#0", "b-3#0"):
        answered.append(path, "answered", item, "done", CITE, "test", None, known)
    return path


def _lines(path):
    with open(path, "rb") as fh:
        return fh.read().split(b"\n")


def _put(path, raw):
    with open(path, "wb") as fh:
        fh.write(b"\n".join(raw))


def _bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def _rewrite(path, index, old, new):
    raw = _lines(path)
    assert old in raw[index], "the fixture line does not hold what the test edits"
    raw[index] = raw[index].replace(old, new)
    _put(path, raw)


def test_item_ids_come_from_the_report_and_the_position(relay):
    its, n = items.load(str(relay))
    assert n == 3
    assert [it.id for it in its] == ["a-1#0", "a-1#1", "a-2#0", "b-3#0"]
    assert its[0].what_sha == items.what_sha("Merge a-1-first-branch to origin/main and restart the logger")


def test_an_empty_folder_is_refused_not_counted_as_zero(tmp_path):
    with pytest.raises(SystemExit):
        items.load(str(tmp_path))


def test_a_valid_ledger_verifies_and_appending_keeps_it_valid(relay):
    path = _three(relay)
    assert [e["entry"] for e in answered.verify(path)] == ["L0001", "L0002", "L0003"]
    before = _bytes(path)
    answered.append(path, "note", "a-1#1", "half done", CITE, "test", None, _known(relay))
    after = _bytes(path)
    assert after.startswith(before) and len(after) > len(before)
    assert len(answered.verify(path)) == 4


def test_a_line_rewritten_in_place_breaks_the_chain(relay):
    path = _three(relay)
    _rewrite(path, -4, b'"answer":"done"', b'"answer":"DONE"')   # L0001; raw[-1] is the empty tail
    with pytest.raises(answered.LedgerBroken, match="rewritten in place"):
        answered.verify(path)


def test_a_rewritten_line_stops_the_next_append(relay):
    path = _three(relay)
    _rewrite(path, -3, b'"by":"test"', b'"by":"ethan"')          # L0002
    size = os.path.getsize(path)
    with pytest.raises(answered.LedgerBroken):
        answered.append(path, "answered", "a-1#1", "x", CITE, "test", None, _known(relay))
    assert os.path.getsize(path) == size
    assert not os.path.exists(path + ".lock")


def test_a_removed_line_and_a_reordered_line_both_fail(relay):
    path = _three(relay)
    raw = _lines(path)
    _put(path, raw[:-3] + raw[-2:])                               # drop L0002
    with pytest.raises(answered.LedgerBroken):
        answered.verify(path)
    _put(path, raw[:-4] + [raw[-3], raw[-4]] + raw[-2:])          # swap L0001 and L0002
    with pytest.raises(answered.LedgerBroken):
        answered.verify(path)
    _put(path, raw)
    assert len(answered.verify(path)) == 3                        # restored, it verifies again


def test_an_edited_header_fails(relay):
    path = _three(relay)
    raw = _lines(path)
    assert raw[0].startswith(b"#")
    raw[0] += b" edited"
    _put(path, raw)
    with pytest.raises(answered.LedgerBroken):
        answered.verify(path)


def test_the_last_line_is_only_protected_by_a_pin(relay):
    """The chain cannot see an edit to the newest line - nothing follows it. This
    asserts the gap as well as the guard, so nobody reads the chain as covering it."""
    path = _three(relay)
    entries = answered.verify(path)
    pins = [{"seq": 3, "entry": "L0003", "line_sha": entries[-1]["_line_sha"]}]
    assert answered.check_pins(entries, pins) == 1
    _rewrite(path, -2, b'"answer":"done"', b'"answer":"DONE"')   # L0003, the last line
    edited = answered.verify(path)                                # the chain alone passes
    with pytest.raises(answered.LedgerBroken, match="rewritten in place"):
        answered.check_pins(edited, pins)
    with pytest.raises(answered.LedgerBroken, match="removed"):
        answered.check_pins(edited[:2], pins)


def test_no_citation_no_answer(relay):
    path = _three(relay)
    before = _bytes(path)
    for cite in (None, {"type": "commit", "ref": ""}, {"type": "hearsay", "ref": "x"}):
        with pytest.raises(ValueError, match="citation"):
            answered.append(path, "answered", "a-1#1", "trust me", cite, "test", None, _known(relay))
    with pytest.raises(ValueError, match="no such item"):
        answered.append(path, "answered", "a-9#0", "x", CITE, "test", None, _known(relay))
    assert _bytes(path) == before
    assert "a-1#1" not in answered.answered_ids(answered.verify(path))


def test_a_correction_is_a_new_line_and_the_old_one_stays(relay):
    path = _three(relay)
    known = _known(relay)
    before = _bytes(path)
    assert "a-1#0" in answered.answered_ids(answered.verify(path))
    answered.append(path, "reopened", "a-1#0", "the merge was reverted", None, "test", "L0001", known)
    assert "a-1#0" not in answered.answered_ids(answered.verify(path))
    assert _bytes(path).startswith(before)                        # L0001 is still there, byte for byte
    answered.append(path, "answered", "a-1#0", "merged again", CITE, "test", None, known)
    assert "a-1#0" in answered.answered_ids(answered.verify(path))
    with pytest.raises(ValueError, match="supersedes"):
        answered.append(path, "reopened", "b-3#0", "x", None, "test", "L0001", known)   # L0001 is another item's


def test_a_note_does_not_close_an_item(relay):
    path = str(relay / answered.LEDGER)
    answered.append(path, "note", "a-1#1", "half done", CITE, "test", None, _known(relay))
    assert answered.answered_ids(answered.verify(path)) == set()


def test_the_registered_rule_is_the_one_in_the_code():
    assert items.THRESHOLD == 0.50 and items.SENSITIVITY == (0.40, 0.60)
    assert items.tokens("Merge a-68-prop-mapping-collapse") == items.tokens("merge A-68")
    assert "the" not in items.tokens("the logger") and "u_a_68" in items.tokens("a-68")


def test_duplicates_cluster_and_strangers_do_not(relay):
    its, _ = items.load(str(relay))
    groups = [[it.id for it in g] for g in items.cluster(its)]
    assert len(groups) == 3
    assert sorted(next(g for g in groups if len(g) == 2)) == ["a-1#0", "a-2#0"]
    assert len(items.cluster(its, 0.999)) == 4                    # the threshold is what joins them


def test_the_reader_prints_open_items_by_cluster_with_the_count(relay, capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(answered, "PINS", str(tmp_path / "no-pins.jsonl"))
    assert answered.main(["--relay", str(relay)]) == 0
    assert capsys.readouterr().out.startswith("4 open items in 3 clusters")
    path = str(relay / answered.LEDGER)
    answered.append(path, "answered", "a-1#0", "merged", CITE, "test", None, _known(relay))
    assert answered.main(["--relay", str(relay)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("3 open items in 3 clusters")
    assert "] a-2#0" in out and "] a-1#0" not in out              # the cluster-mate is NOT closed by inference
    with open(path, "ab") as fh:
        fh.write(b'{"seq":2}\n')                                  # a hand-written line
    assert answered.main(["--relay", str(relay)]) == 2
    assert "LEDGER BROKEN" in capsys.readouterr().err


def test_the_cli_refuses_to_write_without_saying_who(relay, monkeypatch, tmp_path):
    monkeypatch.setattr(answered, "PINS", str(tmp_path / "no-pins.jsonl"))
    args = ["answer", "--relay", str(relay), "--item", "a-1#0", "--answer", "merged",
            "--cite-type", "commit", "--cite-ref", "d9dd3ba"]
    with pytest.raises(SystemExit):
        answered.main(args)
    assert not os.path.exists(str(relay / answered.LEDGER))
    assert answered.main(args + ["--by", "ethan"]) == 0
    assert answered.verify(str(relay / answered.LEDGER))[0]["by"] == "ethan"


def test_a_report_edited_under_an_id_is_reported(relay, capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(answered, "PINS", str(tmp_path / "no-pins.jsonl"))
    _three(relay)
    assert answered.main(["verify", "--relay", str(relay)]) == 0
    capsys.readouterr()
    _report(relay, "b-3", ["A different question now sits at this position"])
    assert answered.main(["verify", "--relay", str(relay)]) == 3
    assert "DRIFT L0003" in capsys.readouterr().out


def test_the_live_ledger_is_intact_and_holds_every_committed_pin():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    relay = os.environ.get("RELAY_DIR") or os.path.join(os.path.dirname(here), "_relay")
    path = os.path.join(relay, answered.LEDGER)
    if not os.path.exists(path):
        pytest.skip("no live ledger at %s - this checkout is not beside the relay folder" % path)
    entries = answered.verify(path)
    pins = answered.read_pins()
    assert pins, "relay/answered.pins.jsonl is empty: the live ledger's head was never pinned"
    assert answered.check_pins(entries, pins) == len(pins)
    assert answered.drifted(entries, items.load(relay)[0]) == []
