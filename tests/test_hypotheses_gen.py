"""The research record is emitted, not typed. Run: pytest -q tests/test_hypotheses_gen.py

THE LOAD-BEARING TEST HERE IS `test_the_resolver_reads_only_the_named_registry`.
Everything else would also pass against the resolver this module rejected — the
one that scans every registry for the record whose estimate rounds to the
published number. That scan reproduces all seventeen figures today, which is
exactly what makes it dangerous: it identifies a VALUE, and a second record
carrying the same value is indistinguishable from the right one. These tests
have to discriminate between the two designs, or they are testing nothing.

Every fixture writes into `tmp_path`. The real registries are read only by the
drift test, which opens them read-only.
"""
import json
import os

import pytest

from jobs import build_hypotheses as G

ROOT = G.ROOT


# ----------------------------------------------------------------- fixtures

def registry(tmp_path, name, records):
    """A registry file containing exactly `records`. Never the real results dir."""
    p = tmp_path / name
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return p


def record(family="F", name="N", est=1.8125, lo=1.2375, hi=2.4706, **kw):
    rec = {"family": family, "name": name, "est": est, "lo": lo, "hi": hi,
           "se": 0.3355, "p": 6.6e-08, "n": 16, "games": 8, "unit": "pp",
           "role": "search", "population": "nfl_wk1", "note": "",
           "estimable": True, "readable": True, "excludes_zero": True,
           # 2026-09-15T13:37:46Z
           "ts": 1789453066.6132076}
    rec.update(kw)
    return rec


# ------------------------------------------------- a pointer is an identity

def test_a_pointer_resolves_to_its_one_record(tmp_path):
    registry(tmp_path, "h1.jsonl", [record(name="OTHER"), record(), record(family="G")])
    rec = G.resolve(G.Pointer("h1.jsonl", "F", "N", 2), results_dir=str(tmp_path))
    assert (rec["family"], rec["name"]) == ("F", "N")


def test_a_pointer_matching_NOTHING_raises(tmp_path):
    """The measurement was renamed or never computed. Raising is the whole
    point: the alternative is publishing a stale literal forever."""
    registry(tmp_path, "h1.jsonl", [record(name="OTHER")])
    with pytest.raises(G.Unresolvable) as e:
        G.resolve(G.Pointer("h1.jsonl", "F", "N", 2), results_dir=str(tmp_path))
    assert "0 records match" in str(e.value)


def test_a_pointer_matching_TWO_raises_rather_than_choosing(tmp_path):
    """A registry is append-only, so a key CAN collide. It must fail loudly -
    silently taking the first (or the last, or the closest) is the identity
    defect this design exists to avoid."""
    registry(tmp_path, "h1.jsonl", [record(), record(est=99.0)])
    with pytest.raises(G.Unresolvable) as e:
        G.resolve(G.Pointer("h1.jsonl", "F", "N", 2), results_dir=str(tmp_path))
    assert "2 records match" in str(e.value)


def test_the_resolver_reads_ONLY_the_named_registry(tmp_path):
    """THE EXPLICIT-INPUTS PROPERTY, and the one assertion a value-scan fails.

    The same (family, name) exists in a second registry with a DIFFERENT
    estimate. A glob over results/*.jsonl would see two records and either
    raise or pick; the pointer names one file, so it sees exactly one and the
    other cannot change what this file publishes.

    Discriminating half: pointing at the other file returns the other number,
    which proves the first half is not passing because the second file was
    unreadable.
    """
    registry(tmp_path, "h1.jsonl", [record(est=1.8125)])
    registry(tmp_path, "scan.jsonl", [record(est=42.0)])
    p = str(tmp_path)
    assert G.resolve(G.Pointer("h1.jsonl", "F", "N", 2), results_dir=p)["est"] == 1.8125
    assert G.resolve(G.Pointer("scan.jsonl", "F", "N", 2), results_dir=p)["est"] == 42.0


def test_a_missing_registry_file_raises(tmp_path):
    with pytest.raises(G.Unresolvable) as e:
        G.resolve(G.Pointer("nope.jsonl", "F", "N", 2), results_dir=str(tmp_path))
    assert "no registry file" in str(e.value)


def test_a_record_that_is_not_estimable_raises(tmp_path):
    """`estimable: false` means the interval could not be computed. Publishing
    its absent `est` would be publishing a KeyError as a finding."""
    registry(tmp_path, "h1.jsonl", [{"family": "F", "name": "N", "estimable": False,
                                     "role": "search", "ts": 1789453066.6}])
    with pytest.raises(G.Unresolvable) as e:
        G.resolve(G.Pointer("h1.jsonl", "F", "N", 2), results_dir=str(tmp_path))
    assert "not estimable" in str(e.value)


# --------------------------------------------------------- what a figure is

def test_the_figure_is_the_record_rounded_for_display(tmp_path):
    registry(tmp_path, "h1.jsonl", [record()])
    f = G.figure(G.Pointer("h1.jsonl", "F", "N", 2), results_dir=str(tmp_path))
    assert f["estimate"] == 1.81
    assert f["interval"] == [1.24, 2.47]
    assert f["n"] == 16 and f["games"] == 8 and f["unit"] == "pp"


def test_rounding_is_per_pointer_and_does_not_touch_the_registry(tmp_path):
    """A pp figure shows 2dp and a Brier difference 4dp, so `digits` cannot be
    a module constant. The registry keeps full precision either way."""
    p = registry(tmp_path, "h1.jsonl", [record()])
    before = p.read_bytes()
    four = G.figure(G.Pointer("h1.jsonl", "F", "N", 4), results_dir=str(tmp_path))
    assert four["estimate"] == 1.8125
    assert G.figure(G.Pointer("h1.jsonl", "F", "N", 2),
                    results_dir=str(tmp_path))["estimate"] == 1.81
    assert p.read_bytes() == before, "the generator must never write to a registry"


def test_the_date_is_when_the_interval_was_COMPUTED(tmp_path):
    """Not when someone wrote it down. `ts` is the field bar 1 of the sweep
    compares against the candidates doc's commit time, so it is the honest one."""
    registry(tmp_path, "h1.jsonl", [record()])
    f = G.figure(G.Pointer("h1.jsonl", "F", "N", 2), results_dir=str(tmp_path))
    assert f["date"] == "2026-09-15"


# ------------------------------------------------------ the emitted document

def test_every_record_carries_exactly_the_contract_fields():
    """`Hypothesis` sets additionalProperties: false, so an extra key fails the
    export - which is also why the pointer cannot ride along in the file."""
    doc = G.build()
    for h in doc["hypotheses"]:
        assert tuple(h) == G.FIELDS, h["id"]


def test_the_verdict_vocabulary_is_closed():
    for h in G.build()["hypotheses"]:
        assert h["verdict"] in {"retired", "null", "not_testable", "open"}, h["id"]


def test_every_record_names_a_script_that_exists():
    """CLAUDE.md: anything quoted as a finding must have a committed script."""
    for h in G.build()["hypotheses"]:
        assert os.path.exists(os.path.join(ROOT, h["script"])), h["id"]


def test_intervals_are_ordered_or_absent():
    for h in G.build()["hypotheses"]:
        assert h["interval"] is None or h["interval"][0] <= h["interval"][1], h["id"]


def test_R11_comes_from_the_registry_and_not_from_a_literal():
    """The increment itself. If someone re-types R11's numbers into the spec,
    the pointer disappears and this fails."""
    r11 = next(h for h in G.HYPOTHESES if h["id"] == "R11")
    assert "figure" in r11, "R11 must carry a Pointer, not literal numbers"
    for k in ("estimate", "interval", "n", "games", "date", "unit"):
        assert k not in r11, f"R11 still hard-codes {k}"
    assert r11["figure"].registry == "h1.jsonl"


def test_R11s_published_figure_reproduces_the_real_registry_record():
    """Against the committed registry, not a fixture: the published 1.81
    [1.24, 2.47] n=16 games=8 must be what h1.jsonl actually holds."""
    built = next(h for h in G.build()["hypotheses"] if h["id"] == "R11")
    assert built["estimate"] == 1.81
    assert built["interval"] == [1.24, 2.47]
    assert built["n"] == 16 and built["games"] == 8


# ------------------------------------------------------------- the drift gate

def test_the_committed_file_is_what_the_generator_emits():
    """The gate that makes this a generator rather than a suggestion. If it
    fails, run `python -m jobs.build_hypotheses` - do not edit the JSON."""
    assert G.main(["--check"]) == 0


def test_check_reports_drift_rather_than_silently_passing(tmp_path):
    """Discriminating: --check must return 1 on a file that differs, or the
    test above is asserting nothing."""
    bad = tmp_path / "hypotheses.json"
    bad.write_text('{"note": "x", "hypotheses": []}', encoding="utf-8")
    assert G.main(["--check", "--out", str(bad)]) == 1
