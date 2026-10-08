"""The answered ledger: the one place a `needs_ethan` item is recorded as answered.

    python -m relay.answered                    open items by cluster, newest first
    python -m relay.answered --track a          one track's
    python -m relay.answered summary            the counts and nothing else
    python -m relay.answered clusters           every cluster, answered ones included
    python -m relay.answered verify             the chain and the committed pins
    python -m relay.answered answer --item a-68#0 --cite-type commit --cite-ref d9dd3ba \
        --answer "merged" [--by ethan] [--supersedes L0004]
    python -m relay.answered note   --item a-21#1 --cite-type commit --cite-ref <sha> --answer "..."
    python -m relay.answered reopen --item a-68#0 --answer "why" [--supersedes L0004]
    python -m relay.answered pin                append the current head to the committed pins

The relay folder is `--relay`, else `RELAY_DIR`, else `../_relay` beside this
checkout; it refuses when none of those is a folder.

THE FILE is `<relay>/ANSWERED.jsonl`. `#` lines at the top are its header; every
other line is one JSON entry. APPEND-ONLY: no line is ever rewritten or removed.
Each entry carries `prev`, the SHA-256 (16 hex) of the line before it, so a line
edited or dropped in place breaks the chain at the next line, and `verify()` -
which every read and every append runs first - refuses. The last line has no
successor to break, so `pin` records the head in `relay/answered.pins.jsonl`,
which IS committed; `tests/test_answered_ledger.py` checks every pin against the
live file.

AN ITEM is `<unit_id>#<index>` (see `relay.items`). AN ENTRY is `L<seq>`.

    answered   closes the item. A citation is REQUIRED: a commit, a later
               report, a log line, a ledger line, or Ethan's own word.
    note       cited evidence that does NOT close the item (half an ask done).
    reopened   the item is open again.

SUPERSESSION. An entry naming `supersedes: "L0004"` replaces L0004, which stops
counting; L0004 stays in the file. An item's state is its last entry that
nothing supersedes, ignoring notes: `answered` is answered, `reopened` or no
entry is open. To withdraw a wrong answer, append `reopened` superseding it.

WHAT THIS DOES NOT DO. It does not decide anything. Nothing here infers an
answer from silence, from a later report not repeating an ask, or from a
cluster-mate being answered: every item is closed by its own cited entry.
"""
import argparse
import collections
import datetime as dt
import hashlib
import json
import os
import sys
import time

from relay import items as items_mod

LEDGER = "ANSWERED.jsonl"
PINS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "answered.pins.jsonl")
KINDS = ("answered", "note", "reopened")
CITE_TYPES = ("commit", "report", "log", "ledger", "ethan")

HEADER = """\
# ANSWERED.jsonl - the only place a needs_ethan item is recorded as answered.
# APPEND-ONLY. No line is ever rewritten or removed. Write through
#   python -m relay.answered answer|note|reopen ...   (repo: calibrated-sports, relay/answered.py)
# never by hand: each line carries `prev`, the hash of the line before it, and a
# hand edit breaks the chain for every reader.
#
# item        <unit_id>#<index> - the report's unit_id and the entry's 0-based position in
#             that report's needs_ethan array (reports/json/<unit_id>.json). a-68#0 is the
#             first item a-68 raised. what_sha is 12 hex of sha256(what), to catch an edit.
# entry       L<seq>, this line's own id.
# kind        answered (closes the item; a citation is required) | note (cited evidence
#             that does not close it) | reopened (open again).
# cite        {type: commit|report|log|ledger|ethan, ref, detail}.
# supersedes  the entry id this line replaces. The replaced line stays in the file and
#             stops counting. A correction is ALWAYS a new line. An item's state is its
#             last un-superseded answered/reopened entry; none means open.
# by          who recorded the line, not who answered. Answering is Ethan's.
"""


class LedgerBroken(Exception):
    pass


def relay_dir(arg=None):
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for cand in (arg, os.environ.get("RELAY_DIR"), os.path.join(os.path.dirname(here), "_relay")):
        if cand and os.path.isdir(cand):
            return cand
    raise SystemExit("no relay folder: pass --relay or set RELAY_DIR")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()[:16]


def _raw_lines(path):
    with open(path, "rb") as fh:
        data = fh.read()
    if data and not data.endswith(b"\n"):
        raise LedgerBroken("%s does not end in a newline: a torn or hand-edited last line" % path)
    return data.split(b"\n")[:-1]


def verify(path):
    """Entries in file order. Raises LedgerBroken naming the first bad line."""
    if not os.path.exists(path):
        return []
    raw = _raw_lines(path)
    n_head = 0
    while n_head < len(raw) and raw[n_head].startswith(b"#"):
        n_head += 1
    prev, entries = _sha(b"\n".join(raw[:n_head])), []
    for lineno, line in enumerate(raw[n_head:], start=n_head + 1):
        try:
            e = json.loads(line.decode("utf-8"))
        except ValueError:
            raise LedgerBroken("line %d is not JSON" % lineno)
        seq = len(entries) + 1
        if e.get("seq") != seq or e.get("entry") != "L%04d" % seq:
            raise LedgerBroken("line %d: expected entry L%04d, found %r - a line was removed or reordered"
                               % (lineno, seq, e.get("entry")))
        if e.get("prev") != prev:
            raise LedgerBroken("line %d (%s): prev does not match the line above - line %d was rewritten in place"
                               % (lineno, e["entry"], lineno - 1))
        e["_line_sha"] = _sha(line)
        entries.append(e)
        prev = e["_line_sha"]
    return entries


def state(entries):
    """{item id: its effective answered/reopened entry}, and {item id: [notes]}."""
    dead = {e["supersedes"] for e in entries if e.get("supersedes")}
    last, notes = {}, collections.defaultdict(list)
    for e in entries:
        if e["entry"] in dead:
            continue
        if e["kind"] == "note":
            notes[e["item"]].append(e)
        else:
            last[e["item"]] = e
    return last, notes


def answered_ids(entries):
    return {i for i, e in state(entries)[0].items() if e["kind"] == "answered"}


def append(path, kind, item, answer, cite=None, by="unknown", supersedes=None, known=None, now=None):
    """Append one entry. `known` is {item id: Item}; an id outside it is refused,
    because an answer to an item that does not exist closes nothing and reads
    as though it did."""
    if kind not in KINDS:
        raise ValueError("kind must be one of %s" % (KINDS,))
    if known is not None and item not in known:
        raise ValueError("no such item %r in the reports" % item)
    if not (answer or "").strip():
        raise ValueError("an entry needs its answer text")
    if kind in ("answered", "note"):
        if not cite or cite.get("type") not in CITE_TYPES or not (cite.get("ref") or "").strip():
            raise ValueError("%s needs a citation: type in %s and a ref. Without one the item stays open."
                             % (kind, CITE_TYPES))
    lock = path + ".lock"
    for _ in range(100):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            time.sleep(0.1)
    else:
        raise RuntimeError("%s is held; if no append is running, remove it" % lock)
    try:
        if not os.path.exists(path):
            with open(path, "xb") as fh:
                fh.write(HEADER.encode("utf-8"))
        entries = verify(path)
        if supersedes:
            target = next((e for e in entries if e["entry"] == supersedes), None)
            if target is None or target["item"] != item:
                raise ValueError("supersedes %r: no such entry for item %s" % (supersedes, item))
        raw = _raw_lines(path)
        seq = len(entries) + 1
        e = {"seq": seq, "entry": "L%04d" % seq, "kind": kind, "item": item,
             "what_sha": known[item].what_sha if known is not None else None,
             "answer": answer.strip(), "cite": cite, "by": by, "supersedes": supersedes,
             "recorded_ts": int(time.time() if now is None else now), "prev": _sha(raw[-1]) if entries
             else _sha(b"\n".join(raw))}
        line = json.dumps(e, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        with open(path, "ab") as fh:
            fh.write(line + b"\n")
        return e
    finally:
        os.close(fd)
        os.remove(lock)


def read_pins(path=None):
    path = PINS if path is None else path
    if not os.path.exists(path):
        return []
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def check_pins(entries, pins):
    """Every pinned line must still be in the ledger, byte for byte."""
    for p in pins:
        if p["seq"] > len(entries):
            raise LedgerBroken("pin %s: the ledger has only %d entries - lines were removed"
                               % (p["entry"], len(entries)))
        if entries[p["seq"] - 1]["_line_sha"] != p["line_sha"]:
            raise LedgerBroken("pin %s: that line no longer hashes to what was pinned - rewritten in place"
                               % p["entry"])
    return len(pins)


def view(relay, track=None):
    """(clusters, entries, items): each cluster is (name, [Item], [open Item])."""
    its, n_reports = items_mod.load(relay)
    entries = verify(os.path.join(relay, LEDGER))
    done = answered_ids(entries)
    out = []
    for g in items_mod.cluster(its):
        if track and not any(it.track == track for it in g):
            continue
        out.append((items_mod.cluster_name(g), g, [it for it in g if it.id not in done]))
    return out, entries, its, n_reports


def _day(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d")


def _summary(clusters, entries, its, n_reports):
    open_items = sum(len(o) for _, _, o in clusters)
    open_clusters = sum(1 for _, _, o in clusters if o)
    total = sum(len(g) for _, g, _ in clusters)
    return ("%d open items in %d clusters  (%d raised in %d clusters, %d answered, %d ledger entries, %d reports)"
            % (open_items, open_clusters, total, len(clusters), total - open_items, len(entries), n_reports))


def _print_open(clusters, entries, limit, show_all):
    _, notes = state(entries)
    last, _ = state(entries)
    shown = 0
    for name, group, open_ in clusters:
        if not open_ and not show_all:
            continue
        if limit and shown >= limit:
            print("... %d more clusters (raise --limit)" % (sum(1 for _, _, o in clusters if o or show_all) - shown))
            break
        shown += 1
        newest = max(it.raised_ts for it in group)
        print("\n%s  newest %s  open %d of %d" % (name, _day(newest), len(open_), len(group)))
        for it in sorted(group, key=lambda it: -it.raised_ts):
            e = last.get(it.id)
            closed = e is not None and e["kind"] == "answered"
            if closed and not show_all:
                continue
            flag = "".join(f for f, on in (("!", it.irreversible), ("$", it.spends_money)) if on)
            mark = "x" if closed else " "
            print("  [%s] %-8s %s %-2s %s" % (mark, it.id, _day(it.raised_ts), flag, it.what))
            if closed:
                print("        %s %s:%s  %s" % (e["entry"], e["cite"]["type"], e["cite"]["ref"], e["answer"]))
            else:
                print("        would do: %s" % it.recommended)
            for n in notes.get(it.id, []):
                print("        note %s %s:%s  %s" % (n["entry"], n["cite"]["type"], n["cite"]["ref"], n["answer"]))


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m relay.answered", description=__doc__.split("\n\n")[0])
    p.add_argument("command", nargs="?", default="open",
                   choices=("open", "summary", "clusters", "verify", "answer", "note", "reopen", "pin"))
    p.add_argument("--relay")
    p.add_argument("--track", choices=("a", "b", "c", "f"))
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--item")
    p.add_argument("--answer")
    p.add_argument("--cite-type", choices=CITE_TYPES)
    p.add_argument("--cite-ref")
    p.add_argument("--cite-detail", default="")
    p.add_argument("--by", help="who is recording the line: a unit id, or ethan. Required to write.")
    p.add_argument("--supersedes")
    a = p.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    relay = relay_dir(a.relay)
    path = os.path.join(relay, LEDGER)
    try:
        if a.command in ("answer", "note", "reopen"):
            if not a.by:
                raise SystemExit("--by is required to write: a unit id, or ethan")
            its, _ = items_mod.load(relay)
            kind = {"answer": "answered", "note": "note", "reopen": "reopened"}[a.command]
            cite = ({"type": a.cite_type, "ref": a.cite_ref, "detail": a.cite_detail}
                    if a.cite_type or a.cite_ref else None)
            e = append(path, kind, a.item, a.answer, cite, a.by, a.supersedes, {it.id: it for it in its})
            print("%s %s %s" % (e["entry"], e["kind"], e["item"]))
            return 0
        entries = verify(path)
        pinned = check_pins(entries, read_pins())
        if a.command == "verify":
            print("chain intact: %d entries, %d pins checked, head %s"
                  % (len(entries), pinned, entries[-1]["_line_sha"] if entries else "(empty)"))
            return 0
        if a.command == "pin":
            if not entries:
                raise SystemExit("nothing to pin")
            head = entries[-1]
            if any(q["seq"] == head["seq"] for q in read_pins()):
                print("already pinned at %s" % head["entry"])
                return 0
            with open(PINS, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps({"seq": head["seq"], "entry": head["entry"], "line_sha": head["_line_sha"],
                                     "pinned_ts": int(time.time())}, sort_keys=True) + "\n")
            print("pinned %s %s - commit relay/answered.pins.jsonl" % (head["entry"], head["_line_sha"]))
            return 0
        clusters, entries, its, n_reports = view(relay, a.track)
        print(_summary(clusters, entries, its, n_reports))
        if a.command != "summary":
            _print_open(clusters, entries, a.limit, a.command == "clusters")
        return 0
    except LedgerBroken as exc:
        print("LEDGER BROKEN: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
