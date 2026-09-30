"""a-57: the record - three tiers, one source each, never blended.

Every guard here is shown answering both ways: the thing it refuses, and the
neighbouring thing it lets through, so no assertion can pass against a stand-in.
"""
import ast
import inspect
import json
import os
import subprocess

import pytest

from core import board as B
from core import record as R
from jobs import export_web as E
from jobs import record_export as X
from jobs import source_registry as SR

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KICK = 1_790_000_000.0          # 2026-09-21T13:33:20Z


def iso(ts):
    return X.iso(ts)


def pub(lid, week=3, read_ts=KICK - 3600, kick=KICK, side="over", price=-110.0, game="2026_03_A_B"):
    ev = {c: None for c in B.LEDGER_COLUMNS}
    ev.update(event="published", lean_id=lid, claim_id=f"{game}:{lid}:receptions",
              row_id=f"r-{lid}", season=2026, week=week, game_id=game, gsis_id=f"00-{lid}",
              market="receptions", line=3.5, side=side, read_at=iso(read_ts), kickoff_ts=kick,
              mkt_p_over=0.5, mkt_books=3, model_p_over=0.56, gap_pp=6.0, price=price,
              band="6-8", model_version="m", lean_threshold_pp=4.0, event_at=iso(read_ts))
    return ev


def graded(p, result, at=None):
    return dict(p, event="graded", result=result, actual=4.0,
                event_at=iso(at or p["kickoff_ts"] + 20_000))


def void(p, reason="inactive"):
    return dict(p, event="void", void_reason=reason, event_at=iso(p["kickoff_ts"] + 20_000))


# =============================================================================
# 1. one source per builder, and the job driven with the other two absent
# =============================================================================

def test_each_builder_takes_exactly_one_source():
    """The builders' parameters: one source each, plus non-source arguments."""
    assert list(inspect.signature(R.build_published).parameters) == ["ledger", "now_ts", "chain", "source"]
    assert list(inspect.signature(R.build_research).parameters) == ["docs", "declarations"]
    assert list(inspect.signature(R.build_backtest).parameters) == ["text", "source"]


def test_no_builder_calls_another_tiers_builder_or_loader():
    """By AST, not grep: a builder's body names no other tier's builder, and
    core.record reads nothing from disk at all."""
    tree = ast.parse(open(os.path.join(ROOT, "core", "record.py"), encoding="utf-8").read())
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    builders = {"build_published", "build_research", "build_backtest"}
    for name in builders:
        called = {n.func.id for n in ast.walk(funcs[name])
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert not (called & (builders - {name})), f"{name} calls {called & builders}"
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "open" not in names and "polars" not in names and "sqlite3" not in names


def _ledger_tree(tmp_path, rows):
    import polars as pl
    d = tmp_path / "board" / "board" / "nfl"
    d.mkdir(parents=True)
    pl.DataFrame(rows, schema={c: (pl.Int64 if t == "int64" else pl.Float64 if t == "float64" else pl.Utf8)
                               for c, t in B.LEDGER_DTYPES.items()}).write_parquet(d / "ledger.parquet")
    return str(tmp_path / "board")


def test_published_builds_with_the_other_two_sources_absent(tmp_path):
    """root is NOT a git repository and holds no documents: if the published
    part touched a research or backtest source it would raise."""
    p = pub("a1")
    board = _ledger_tree(tmp_path, [p, graded(p, "cleared")])
    nogit = tmp_path / "nogit"
    nogit.mkdir()
    files, unbuilt = X.build({"published"}, KICK + 90_000, board, root=str(nogit))
    assert list(files) == ["record/nfl/published.json"] and unbuilt == []
    f = files["record/nfl/published.json"]
    assert f["tier"] == "published" and f["n_graded"] == 1
    for foreign in ("rows", "statement", "register_figure", "by_verdict", "population"):
        assert foreign not in f
    # ...and the research part, with the same absent sources, does raise
    with pytest.raises(RuntimeError):
        X.build({"research"}, KICK, board, root=str(nogit))


def _git_repo(tmp_path, files):
    root = tmp_path / "repo"
    root.mkdir()
    run = lambda *a: subprocess.run(["git", *a], cwd=root, check=True, capture_output=True)
    run("init", "-q")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    run("add", *files)
    run("commit", "-q", "-m", "docs")
    return str(root)


FINDINGS = """# X
Pre-registration `docs/C99-thing-preregistration.md` at `abc1234`.
The primary interval is **-1.20pp [-3.40, +1.00]** over 12 games, 345 rungs.
"""
PREREG = "# C99 - pre-registration\nRetire if the estimate is at or below zero.\n"
DECL = {"rows": [{"id": "C99", "prereg": "docs/C99-thing-preregistration.md",
                  "findings": "docs/findings/thing.md", "question": "Is the thing real?",
                  "verdict": "retired",
                  "headline": {"label": "gap", "quote": "**-1.20pp [-3.40, +1.00]**", "unit": "pp"},
                  "power": "Retired on 12 games and 345 rungs.", "note": None}]}


def test_research_and_backtest_build_with_no_ledger(tmp_path):
    backtest = open(os.path.join(ROOT, "docs", "findings", "ranking-versus-calibration.md"),
                    encoding="utf-8").read()
    root = _git_repo(tmp_path, {"docs/C99-thing-preregistration.md": PREREG,
                                "docs/findings/thing.md": FINDINGS,
                                "docs/findings/ranking-versus-calibration.md": backtest,
                                "docs/record/research-verdicts.json": json.dumps(DECL)})
    files, _ = X.build({"research", "backtest"}, KICK, board_dir=None, root=root)
    assert set(files) == {"record/research.json", "record/backtest.json"}
    for f in files.values():
        assert "leans" not in f and "n_published" not in f and "record" not in f
    assert files["record/research.json"]["rows"][0]["registered_at"]["commit"] == \
        subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True).stdout.strip()
    # and the published part with no Board tree is UNBUILT and named, never filled
    files, unbuilt = X.build({"published"}, KICK, board_dir=None, root=root)
    assert files == {} and unbuilt and "Board" in unbuilt[0]


def test_no_record_file_carries_a_figure_spanning_tiers():
    """No key anywhere in a record file names an accuracy or a combined score."""
    doc = open(os.path.join(ROOT, "docs", "findings", "ranking-versus-calibration.md"), encoding="utf-8").read()
    p = pub("a1")
    bodies = [R.build_published([p, graded(p, "cleared")], KICK + 90_000),
              R.build_backtest(doc, {"doc": "d"})]

    def keys(o):
        if isinstance(o, dict):
            for k, v in o.items():
                yield k
                yield from keys(v)
        elif isinstance(o, list):
            for v in o:
                yield from keys(v)
    for b in bodies:
        bad = [k for k in keys(b) if any(w in k.lower() for w in ("accuracy", "combined", "overall", "pooled_all"))]
        assert bad == [], bad


# =============================================================================
# 2. a reconstructed row cannot reach published.json
# =============================================================================

def test_a_row_read_after_kickoff_is_excluded_not_published():
    early = pub("ok1")
    late = pub("late", read_ts=KICK + 60)                 # read_at AFTER its kickoff_ts
    body = R.build_published([early, late, graded(early, "cleared"), graded(late, "cleared")],
                             KICK + 90_000)
    ex = body["excluded_not_pre_kickoff"]
    assert ex["n"] == 1 and ex["rows"][0]["lean_id"] == "late"
    assert "not before kickoff" in ex["rows"][0]["reason"]
    assert ex["rows"][0]["terminal_event"] == "graded"
    assert [x["lean_id"] for x in body["leans"]] == ["ok1"]
    assert body["n_published"] == 1 and body["record"]["cleared"] == 1


def test_event_at_after_kickoff_is_excluded_too_and_at_kickoff_is_not_before():
    a = pub("evt")
    a["event_at"] = iso(KICK + 5)                           # read claims early, written late
    b = pub("tie", read_ts=KICK)                            # exactly AT kickoff
    body = R.build_published([a, b], KICK - 10)
    assert sorted(r["lean_id"] for r in body["excluded_not_pre_kickoff"]["rows"]) == ["evt", "tie"]
    assert body["n_published"] == 0


def test_unparseable_read_at_is_excluded():
    a = pub("bad")
    a["read_at"] = "yesterday"
    body = R.build_published([a], KICK - 10)
    assert body["excluded_not_pre_kickoff"]["n"] == 1


# =============================================================================
# 3. the record against break-even, and the week-block interval
# =============================================================================

def test_record_is_measured_against_breakeven_at_the_published_price():
    ps = [pub(f"p{i}", price=-150.0) for i in range(4)]      # break-even 0.6
    body = R.build_published(ps + [graded(ps[0], "cleared"), graded(ps[1], "cleared"),
                                   graded(ps[2], "missed"), void(ps[3])], KICK + 90_000)
    rec = body["record"]
    assert (rec["cleared"], rec["missed"], rec["void"]) == (2, 1, 1)
    assert rec["hit_rate"] == pytest.approx(0.6667, abs=1e-4)
    assert rec["breakeven"] == pytest.approx(0.6)
    assert rec["margin_pp"] == pytest.approx(6.67, abs=0.01)
    assert rec["units"] == pytest.approx(2 * (100 / 150) - 1, abs=1e-3)
    assert body["n_published"] == body["n_graded"] + body["n_void"] + body["n_ungraded"] == 4


def test_a_ledgered_price_inside_plus_minus_100_is_not_a_price():
    """-2.5 read as American odds is a 41x payout. It is published as invalid,
    left out of break-even and units - and a valid neighbour is not."""
    a, b = pub("bad", price=-2.5), pub("good", price=-110.0)
    body = R.build_published([a, b, graded(a, "cleared"), graded(b, "missed")], KICK + 90_000)
    rec = body["record"]
    assert rec["n_price_invalid"] == 1 and rec["n_priced"] == 1
    assert rec["units"] == pytest.approx(-1.0)
    lean = {x["lean_id"]: x for x in body["leans"]}
    assert lean["bad"]["price"] is None and lean["bad"]["price_ledgered"] == -2.5
    assert lean["bad"]["breakeven"] is None and lean["good"]["breakeven"] == pytest.approx(0.5238, abs=1e-4)


def test_one_graded_week_publishes_no_interval_and_says_so():
    ps = [pub(f"g{i}", game=f"2026_03_G{i}_H") for i in range(30)]        # 30 games, ONE week
    ev = ps + [graded(p, "cleared" if i % 2 else "missed") for i, p in enumerate(ps)]
    iv = R.build_published(ev, KICK + 90_000)["record"]["interval"]
    assert iv["n_blocks"] == 1 and iv["block"] == "week"
    assert iv["hit_rate"] is None and iv["margin_pp"] is None
    assert iv["informative"] is False and "one block" in iv["why"]


def test_three_graded_weeks_give_an_informative_interval():
    ev = []
    for w in (3, 4, 5):
        for i in range(10):
            p = pub(f"w{w}-{i}", week=w, kick=KICK + w * 604800, read_ts=KICK + w * 604800 - 3600)
            ev += [p, graded(p, "cleared" if (i + w) % 3 else "missed")]
    iv = R.build_published(ev, KICK + 10 * 604800)["record"]["interval"]
    assert iv["n_blocks"] == 3 and iv["informative"] is True
    lo, hi = iv["hit_rate"]
    assert 0 <= lo <= hi <= 1
    two = [e for e in ev if e["week"] != 5]
    iv2 = R.build_published(two, KICK + 10 * 604800)["record"]["interval"]
    assert iv2["n_blocks"] == 2 and iv2["informative"] is False and iv2["hit_rate"] is not None


def test_chain_is_null_without_a48_and_says_so():
    p = pub("a1")
    body = R.build_published([p], KICK - 10)
    assert body["chain"] is None and "a-48" in body["chain_note"]
    assert "integrity" in body["chain_note"]


# =============================================================================
# 4. research: every retired row carries its power; figures are quoted, not typed
# =============================================================================

def _docs():
    return {"docs/C99-thing-preregistration.md": {"text": PREREG, "commit": "a" * 40,
                                                  "author_date": "2026-09-29T00:00:00Z"},
            "docs/findings/thing.md": {"text": FINDINGS, "commit": "b" * 40,
                                       "author_date": "2026-09-29T01:00:00Z"}}


def test_a_retired_row_without_power_is_refused():
    d = json.loads(json.dumps(DECL["rows"]))
    d[0]["power"] = "  "
    with pytest.raises(R.RecordError, match="bare 'retired'"):
        R.build_research(_docs(), d)
    d[0]["verdict"] = "open"                                  # the neighbour passes
    assert R.build_research(_docs(), d)["rows"][0]["power"] is None


def test_every_retired_row_in_the_real_register_has_power():
    docs, decl = X.load_docs(ROOT)
    body = R.build_research(docs, decl)
    retired = [r for r in body["rows"] if r["verdict"] == "retired"]
    assert retired, "the real register has no retired row - the test would assert nothing"
    for r in retired:
        assert r["power"] and r["power"].strip(), r["id"]


def test_the_headline_is_parsed_from_a_verbatim_quote():
    body = R.build_research(_docs(), DECL["rows"])
    h = body["rows"][0]["headline"]
    assert (h["estimate"], h["lo"], h["hi"]) == (-1.2, -3.4, 1.0)
    d = json.loads(json.dumps(DECL["rows"]))
    d[0]["headline"]["quote"] = "**-1.25pp [-3.40, +1.00]**"          # a figure the doc does not say
    with pytest.raises(R.RecordError, match="not found verbatim"):
        R.build_research(_docs(), d)


def test_a_number_in_a_declared_sentence_must_be_in_the_documents():
    d = json.loads(json.dumps(DECL["rows"]))
    d[0]["power"] = "Retired on 12 games and 999 rungs."
    with pytest.raises(R.RecordError, match="999"):
        R.build_research(_docs(), d)


def test_a_prereg_with_no_declaration_is_published_open_not_dropped():
    body = R.build_research(_docs(), [])
    assert body["n_rows"] == 1 and body["rows"][0]["verdict"] == "open"
    assert body["rows"][0]["classified"] is False and body["unclassified"] == ["docs/C99-thing-preregistration.md"]


def test_parse_figure_ignores_digits_in_the_label():
    assert R.parse_figure("at the end of Q3, the over is priced too high: −4.02pp [−6.92, −0.83]") == \
        (-4.02, -6.92, -0.83)
    assert R.parse_figure("| P1 pooled | 0.530 | 0.573 | **−0.043 [−0.054, −0.033]**") == (-0.043, -0.054, -0.033)


def test_the_real_register_carries_the_five_rows_as_their_reports_state_them():
    docs, decl = X.load_docs(ROOT)
    rows = {r["id"]: r for r in R.build_research(docs, decl)["rows"]}
    assert rows["C20"]["verdict"] == "retired" and "8.9pp" in rows["C20"]["power"]
    assert "does not mean" in rows["C20"]["note"]
    assert rows["C21"]["verdict"] == "retired" and rows["C21-endQ3"]["verdict"] == "open"
    assert rows["C22"]["verdict"] == "retired" and "90%" in rows["C22"]["note"]
    assert rows["C23"]["verdict"] == "stopped_by_gate" and rows["C23"]["headline"] is None
    assert "never measured" in rows["C23"]["note"] and "fairly priced" in rows["C23"]["note"]
    assert rows["C24"]["headline"]["hi"] < 0                           # orders worse
    for r in rows.values():
        assert r["registered_at"]["commit"] and r["registered_at"]["author_date"]


# =============================================================================
# 5. backtest: the restated figure, the superseded pair, one statement string
# =============================================================================

def _backtest():
    doc = open(os.path.join(ROOT, "docs", "findings", "ranking-versus-calibration.md"), encoding="utf-8").read()
    return doc, R.build_backtest(doc, {"doc": "d"})


def test_backtest_register_figure_is_the_restatement_and_the_old_pair_is_superseded():
    _doc, b = _backtest()
    assert b["register_figure"] == {"lo": 0.0195, "hi": 0.0237, "n": 16041,
                                    "text": "+0.0195 to +0.0237 over 16,041"}
    assert sum(s["n"] for s in b["seasons"]) == b["population"]["out_of_sample_outcomes"] == 16041
    old = b["superseded"][0]
    assert (old["lo"], old["hi"], old["n"]) == (0.0218, 0.0272, 14857) and "settlement" in old["reason"]
    assert b["population"]["held_out"]["final_season"] == 2025


def test_the_statement_carries_all_three_clauses_in_one_string():
    _doc, b = _backtest()
    assert [p["clause"] for p in b["statement_parts"]] == ["over_confidence", "constant_base_rate", "ordering"]
    for p in b["statement_parts"]:
        assert p["text"] in b["statement"]
    s = b["statement"]
    assert "over-confidence" in s and "barely beat a constant" in s and "orders outcomes better" in s


def test_each_clause_can_come_out_the_other_way():
    """Falsifiable: the same pipeline, fed figures that point the other way,
    words the clause the other way."""
    corp = {2023: 0.3, 2024: 0.2}
    const = [{"season": 2024, "model_recal_minus_constant": (-0.0003, -0.001, 0.0),
              "close_minus_constant": (-0.02, -0.03, -0.01)}]
    parts = R.statement_parts(corp, const, (0.02, 0.01, 0.03), 0.0195, [2023, 2024])
    assert "NOT calibration" in parts[0]["text"]
    assert "barely" not in parts[1]["text"] and "not small" in parts[1]["text"]
    assert "model orders outcomes better" in parts[2]["text"]
    parts = R.statement_parts(corp, const, (0.0, -0.01, 0.01), 0.0195, [2023, 2024])
    assert "neither orders" in parts[2]["text"]


def test_a_backtest_source_whose_seasons_do_not_sum_is_refused():
    doc, _ = _backtest()
    bad = doc.replace("| P1 2025 | 6,031 | +0.0195 |", "| P1 2025 | 6,000 | +0.0195 |")
    assert bad != doc
    with pytest.raises(R.RecordError, match="do not sum"):
        R.build_backtest(bad, {"doc": "d"})


# =============================================================================
# 6. the export gate covers all three files
# =============================================================================

def test_all_three_files_pass_the_contract_and_the_source_gate(tmp_path):
    p, q = pub("a1"), pub("a2", price=1.5)
    board = _ledger_tree(tmp_path, [p, q, graded(p, "cleared"), void(q)])
    files, unbuilt = X.build(set(R.TIERS), KICK + 90_000, board, root=ROOT)
    assert unbuilt == [] and len(files) == 3
    E.validate_contract(files)
    approved = SR.require_declared(files)
    assert ("nfl", "record.published") in approved and ("nfl", "record.research") in approved


def test_the_contract_refuses_a_file_that_breaks_its_shape():
    p = pub("a1")
    body = R.build_published([p], KICK - 10, source={"table": "board_ledger", "key": "board/nfl/ledger.parquet",
                                                     "rows": 1, "sha256": "0" * 64})
    f = X.envelope("record.published", "nfl", KICK, body)
    E.validate_contract({"record/nfl/published.json": f})
    f2 = dict(f, accuracy=0.53)                                   # an additive, cross-tier figure
    with pytest.raises(E.ContractError):
        E.validate_contract({"record/nfl/published.json": f2})
    f3 = dict(f, kind="record.research")
    with pytest.raises(E.ContractError):
        E.validate_contract({"record/nfl/published.json": f3})


def test_the_job_owns_no_prefix_and_deletes_nothing(tmp_path):
    dest = tmp_path / "web"
    (dest / "record").mkdir(parents=True)
    (dest / "record" / "keep.json").write_text("{}", encoding="utf-8")
    p = pub("a1")
    board = _ledger_tree(tmp_path, [p])
    rc = X.main(["--write", "--dest", str(dest), "--board", board, "--only", "published",
                 "--now", str(KICK - 10)])
    assert rc == 0
    assert (dest / "record" / "keep.json").exists()
    assert (dest / "record" / "nfl" / "published.json").exists()
