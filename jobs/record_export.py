"""The record (unit a-57): three files, three tiers, one source each.

    python -m jobs.record_export --check --board D:/x/board_export     # build, validate, write nothing
    python -m jobs.record_export --write --dest D:/scratch/web --board D:/x/board_export
    python -m jobs.record_export --write --dest D:/scratch/web --only research

    record/nfl/published.json   <- the Board ledger (board/nfl/ledger.parquet) ONLY
    record/research.json        <- tracked docs/*preregistration*.md + docs/findings/*.md,
                                   at HEAD, with docs/record/research-verdicts.json
    record/backtest.json        <- docs/findings/ranking-versus-calibration.md at HEAD ONLY

WHY THREE FILES AND NOT ONE. The published record, the research register and the
backtest answer different questions and are not comparable, so nothing here may
average, pool or weight a number from one with a number from another - and the
simplest way to make that true is that no builder can see a second source. Each
loader below feeds exactly one builder in `core.record`, and a missing source
leaves its file unbuilt (listed, with the reason) rather than filled from another.

THE DOCUMENTS ARE READ AT HEAD, NOT FROM THE WORKING TREE. `git ls-files` picks
the files and `git show HEAD:<path>` reads them, so an uncommitted edit - or an
untracked findings file sitting in a clone - can never reach a published row. A
row's `registered_at` is the commit that ADDED its pre-registration, with that
commit's author date: the claim the register exists to carry is "this was
written down before the result", and the commit is the evidence.

WHERE IT WRITES. Through `export_web.sync_keys(dest, files, [])`: the contract
check and the source gate, and it OWNS NO PREFIX, so it can delete nothing -
the same shape as landing.json. `market_log.db` is not opened at all.

THE CHAIN. When a-48's hash chain is in the tree (`core.board.verify_chain`) and
the ledger carries it, the chain is verified here and its head published; a
BROKEN chain refuses the file. Without a-48, `chain` is null and says so - no
substitute integrity figure is invented.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import time

import config
from core import board as B
from core import record as R

SPORT = "nfl"
KEYS = {"published": f"record/{SPORT}/published.json",
        "research": "record/research.json",
        "backtest": "record/backtest.json"}
LEDGER = os.path.join("board", SPORT, "ledger.parquet")
VERDICTS = "docs/record/research-verdicts.json"
BACKTEST_DOC = "docs/findings/ranking-versus-calibration.md"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def iso(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def envelope(kind, sport, now_ts, body):
    return {"schema_version": 2, "generated_at": iso(now_ts), "kind": kind, "sport": sport, **body}


def _git(*args, root=ROOT):
    r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                       encoding="utf-8")
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout


def head_commit(root=ROOT):
    return _git("rev-parse", "HEAD", root=root).strip()


def added_by(path, root=ROOT):
    """The commit that added `path` and its author date -> (sha, iso). Refuses in
    a shallow clone: there the oldest commit on hand reads as the one that added
    every file, which would stamp a pre-registration with a date it does not have."""
    if _git("rev-parse", "--is-shallow-repository", root=root).strip() == "true":
        raise R.RecordError("a shallow clone cannot date a pre-registration - fetch full history")
    out = _git("log", "--diff-filter=A", "--format=%H %aI", "--", path, root=root).split()
    if len(out) < 2:
        raise R.RecordError(f"{path} has no commit that added it")
    sha, when = out[-2], out[-1]
    utc = dt.datetime.fromisoformat(when).astimezone(dt.timezone.utc)
    return sha, utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def committed_text(path, root=ROOT):
    return _git("show", f"HEAD:{path}", root=root)


# =============================================================================
# the three loaders - one source each
# =============================================================================

def load_ledger(board_dir):
    """-> (ledger rows, source block), or (None, reason). The ledger parquet only."""
    import polars as pl
    if not board_dir:
        return None, "no Board tree was given (--board / BOARD_EXPORT_DIR unset)"
    path = os.path.join(board_dir, LEDGER)
    if not os.path.exists(path):
        return None, f"{LEDGER} is not in {board_dir}"
    with open(path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    rows = pl.read_parquet(path).to_dicts()
    for r in rows:
        for k, v in r.items():
            if isinstance(v, float) and v != v:
                r[k] = None
    return rows, {"table": "board_ledger", "key": f"board/{SPORT}/ledger.parquet",
                  "rows": len(rows), "sha256": digest}


def ledger_chain(rows, now_ts):
    """a-48's chain, verified, or None when a-48 is absent or the ledger predates
    it. A broken chain raises: the record must not be published over it."""
    verify = getattr(B, "verify_chain", None)
    if verify is None:
        return None, R.CHAIN_ABSENT
    rep = verify(rows)
    if rep.reason and rep.first_break is None:
        return None, f"the ledger carries no chain: {rep.statement}"
    if not rep.holds:
        raise R.RecordError(f"refusing to publish the record over a broken ledger: {rep.statement}")
    return ({"head": rep.head["row_hash"] if rep.head else None, "rows_verified": rep.rows,
             "verified_at": iso(now_ts)}, None)


def load_docs(root=ROOT):
    """-> ({path: {text, commit, author_date}}, declarations), every tracked
    pre-registration and findings document, read at HEAD."""
    paths = [p for p in _git("ls-files", "docs", root=root).splitlines()
             if (p.startswith("docs/") and "/" not in p[5:] and "preregistration" in p
                 and p.endswith(".md"))
             or (p.startswith("docs/findings/") and p.endswith(".md") and p.count("/") == 2)]
    docs = {}
    for p in sorted(paths):
        sha, when = added_by(p, root)
        docs[p] = {"text": committed_text(p, root), "commit": sha, "author_date": when}
    decl = json.loads(committed_text(VERDICTS, root))["rows"]
    return docs, decl


def load_backtest(root=ROOT):
    sha, when = added_by(BACKTEST_DOC, root)
    return committed_text(BACKTEST_DOC, root), {"doc": BACKTEST_DOC, "commit": sha,
                                                "author_date": when}


# =============================================================================
# build
# =============================================================================

def build(parts, now_ts, board_dir=None, root=ROOT):
    """-> ({key: payload}, [unbuilt notes]). Each part reads its own source only."""
    files, unbuilt = {}, []
    if "published" in parts:
        rows, src = load_ledger(board_dir)
        if rows is None:
            unbuilt.append(f"{KEYS['published']}: {src}")
        else:
            chain, note = ledger_chain(rows, now_ts)
            body = R.build_published(rows, now_ts, chain=chain, source=src)
            if note and chain is None:
                body["chain_note"] = note
            files[KEYS["published"]] = envelope("record.published", SPORT, now_ts, body)
    if "research" in parts:
        docs, decl = load_docs(root)
        body = R.build_research(docs, decl)
        body["source"] = {"preregistrations": "docs/*preregistration*.md",
                          "findings": "docs/findings/*.md", "verdicts": VERDICTS,
                          "read_at_commit": head_commit(root), "tracked_only": True}
        files[KEYS["research"]] = envelope("record.research", None, now_ts, body)
    if "backtest" in parts:
        text, src = load_backtest(root)
        files[KEYS["backtest"]] = envelope("record.backtest", None, now_ts,
                                           R.build_backtest(text, src))
    return files, unbuilt


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="build and validate; write nothing")
    mode.add_argument("--write", action="store_true")
    ap.add_argument("--dest", help="the export tree to write into (required with --write)")
    ap.add_argument("--board", help="the Board's tree (default BOARD_EXPORT_DIR)")
    ap.add_argument("--only", action="append", choices=R.TIERS)
    ap.add_argument("--now", type=float, help="unix seconds; default the wall clock")
    a = ap.parse_args(argv)
    from jobs import export_web as E
    board = a.board or getattr(config, "BOARD_EXPORT_DIR", None) or os.getenv("BOARD_EXPORT_DIR")
    now = a.now if a.now is not None else time.time()
    files, unbuilt = build(set(a.only or R.TIERS), now, board)
    E.validate_contract(files)
    for k, v in sorted(files.items()):
        extra = ""
        if v["kind"] == "record.published":
            iv = v["record"]["interval"]
            extra = (f" published {v['n_published']} graded {v['n_graded']} void {v['n_void']} "
                     f"ungraded {v['n_ungraded']} excluded {v['excluded_not_pre_kickoff']['n']} "
                     f"n_blocks {iv['n_blocks']} informative {iv['informative']}")
        elif v["kind"] == "record.research":
            extra = f" rows {v['n_rows']} {v['by_verdict']} unclassified {len(v['unclassified'])}"
        elif v["kind"] == "record.backtest":
            extra = f" register {v['register_figure']['text']}"
        print(f"{k}:{extra}")
    for u in unbuilt:
        print(f"UNBUILT {u}")
    if not files:
        print("nothing built", file=sys.stderr)
        return 2
    if a.write:
        if not a.dest:
            ap.error("--write needs --dest")
        written, deleted = E.sync_keys(a.dest, files, [])
        print(f"wrote {written} of {len(files)} (deleted {deleted})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
