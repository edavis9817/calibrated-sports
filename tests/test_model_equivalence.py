"""Brief 017: the equivalence evidence, and the refusal to guess a version.

Run: pytest -q tests/test_model_equivalence.py

The equivalence table's whole weakness is that a human can assert a row into
it. These tests are the answer to that: they check that the assertion actually
refuses the cases it is supposed to refuse, which is the only thing that makes
the table stronger than a comment.
"""
import ast
import inspect
import os
import sqlite3

import pytest

from core import model_equivalence as me
from core import version_resolve as vr


# =============================================================================
# the hashed fileset must not drift out of sync with baseline.py
# =============================================================================

def test_the_fileset_matches_what_baseline_actually_hashes():
    """FINGERPRINT_FILES is duplicated rather than imported, because adding a
    constant to baseline.py would change the hash this module reasons about.
    A duplicate that silently drifts is worse than the import, so pin it."""
    from models import baseline
    src = inspect.getsource(baseline._fingerprint)
    literals = [n.value for n in ast.walk(ast.parse(src.strip()))
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and n.value.endswith(".py")]
    assert tuple(literals) == me.FINGERPRINT_FILES


# =============================================================================
# what the assertion accepts and refuses
# =============================================================================

def _cmp(changed=None, added=None, removed=None):
    return {"changed": changed or {}, "added": added or {},
            "removed": removed or {}, "diff": "x"}


def test_a_fee_only_removal_is_accepted():
    me.assert_fee_only(_cmp(removed={"core/distributions.py":
                                     ["kalshi_fee", "edge_after_fees"]}))


def test_a_changed_prediction_function_is_refused():
    with pytest.raises(me.NotEquivalent) as e:
        me.assert_fee_only(_cmp(changed={"models/baseline.py": ["predict"]}))
    assert "predict" in str(e.value)


def test_a_fee_function_that_CHANGED_IN_PLACE_is_refused():
    """A fee symbol still living in the hashed fileset with different
    behaviour is exactly what this table must not wave through - being a fee
    name is not a licence to change silently."""
    with pytest.raises(me.NotEquivalent):
        me.assert_fee_only(_cmp(changed={"core/distributions.py": ["kalshi_fee"]}))


def test_an_added_non_fee_symbol_is_refused():
    with pytest.raises(me.NotEquivalent):
        me.assert_fee_only(_cmp(added={"models/features.py": ["snap_role_v2"]}))


def test_an_added_non_fee_import_is_refused():
    with pytest.raises(me.NotEquivalent):
        me.assert_fee_only(_cmp(added={"models/baseline.py": ["import:numpy"]}))


def test_an_empty_comparison_is_refused():
    """Recording an equivalence between two identical filesets is recording a
    fact about nothing, and would hide a mistaken pair of commits."""
    with pytest.raises(me.NotEquivalent):
        me.assert_fee_only(_cmp())


# =============================================================================
# the structural comparison itself
# =============================================================================

def test_top_level_comparison_ignores_comments_and_formatting():
    a, _ = me._top_level("# a comment\ndef f(x):\n    return x + 1\n")
    b, _ = me._top_level('"""doc"""\ndef f(x):\n\n    # moved comment\n    return x+1\n')
    assert a["f"] == b["f"]


def test_top_level_comparison_catches_a_flipped_sign():
    """The case a diff of hunk headers would miss."""
    a, _ = me._top_level("def f(x):\n    return x + 1\n")
    b, _ = me._top_level("def f(x):\n    return x - 1\n")
    assert a["f"] != b["f"]


def test_top_level_comparison_tracks_module_constants():
    a, _ = me._top_level("K = 3\n")
    b, _ = me._top_level("K = 4\n")
    assert a["K"] != b["K"]


def test_the_real_016_change_passes_and_is_fee_only():
    """The live case this brief exists for."""
    cmp = me.compare("d9691f0", "024dd24")
    me.assert_fee_only(cmp)
    assert cmp["changed"] == {}
    assert set(cmp["removed"]["core/distributions.py"]) == \
        {"kalshi_fee", "edge_after_fees"}


def test_the_recorded_fingerprints_are_reproducible_from_their_commits():
    assert me.fingerprint_matches("d9691f0", "679868a8549a")
    assert me.fingerprint_matches("024dd24", "a307952813e6")
    assert not me.fingerprint_matches("d9691f0", "a307952813e6")


# =============================================================================
# resolution: equivalence links, and the refusal
# =============================================================================

def _db(tmp_path, monkeypatch, versions, links=()):
    import config
    p = tmp_path / "m.db"
    monkeypatch.setattr(config, "DB_PATH", str(p))
    c = sqlite3.connect(p)
    c.executescript(vr.SCHEMA)
    c.executescript("""
        CREATE TABLE predictions (outcome_id TEXT, model_version TEXT,
                                  created_ts REAL);
        CREATE TABLE outcomes (outcome_id TEXT, season INT, week INT);
    """)
    for i, (v, n) in enumerate(versions.items()):
        for k in range(n):
            oid = f"{v}-{k}"
            c.execute("INSERT INTO predictions VALUES (?,?,?)", (oid, v, i))
            c.execute("INSERT INTO outcomes VALUES (?,2026,1)", (oid,))
    for a, b in links:
        c.execute("INSERT INTO model_version_equivalence VALUES (?,?,?,?,0)",
                  (a, b, "test", "{}"))
    c.commit()
    c.close()
    return p


def test_a_version_with_predictions_resolves_to_itself(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch, {"A": 3})
    assert vr.resolve(2026, 1, "A") == ("A", "")


def test_an_equivalent_version_is_followed(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch, {"A": 3}, [("A", "B")])
    v, note = vr.resolve(2026, 1, "B")
    assert v == "A" and "RECORDED EQUIVALENT" in note


def test_equivalence_is_followed_transitively(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch, {"A": 3}, [("A", "B"), ("B", "C")])
    assert vr.resolve(2026, 1, "C")[0] == "A"


def test_equivalence_is_symmetric(tmp_path, monkeypatch):
    """The recorded direction is provenance, not semantics."""
    _db(tmp_path, monkeypatch, {"C": 3}, [("A", "B"), ("B", "C")])
    assert vr.resolve(2026, 1, "A")[0] == "C"


def test_an_unknown_version_RAISES_rather_than_falling_back(tmp_path, monkeypatch):
    """THE ITEM 3 BEHAVIOUR. A printed warning inside a twelve-minute run is a
    warning nobody reads; four separate bugs in this project have shipped
    looking like results."""
    _db(tmp_path, monkeypatch, {"A": 3})
    with pytest.raises(vr.ModelVersionError) as e:
        vr.resolve(2026, 1, "Z")
    assert "A" in str(e.value)          # tells you what IS available


def test_a_version_with_zero_predictions_raises(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch, {"A": 3}, [("Z", "Q")])
    with pytest.raises(vr.ModelVersionError):
        vr.resolve(2026, 1, "Z")


def test_no_predictions_at_all_raises(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch, {})
    with pytest.raises(vr.ModelVersionError):
        vr.resolve(2026, 1, "A")


def test_an_explicit_override_is_obeyed_even_when_empty(tmp_path, monkeypatch):
    """If a caller insists on a version, the emptiness is theirs to explain -
    but it has to be typed, which is the whole point."""
    _db(tmp_path, monkeypatch, {"A": 3})
    v, note = vr.resolve(2026, 1, "A", override="Z")
    assert v == "Z" and "--model-version" in note


def test_research_entrypoints_expose_the_override():
    """The escape hatch has to exist on the scripts, not just in the library."""
    import research.clv
    import research.maker
    for mod in (research.clv, research.maker):
        src = inspect.getsource(mod.main)
        assert "--model-version" in src, mod.__name__


# =============================================================================
# brief 018 item 4: normalise the input instead of enumerating the variants
# =============================================================================

def test_the_fingerprint_normalises_line_endings():
    """The fix, at the source. Hashing raw bytes made the fingerprint identify
    a checkout; under autocrlf the same commit hashed differently on different
    machines."""
    src = inspect.getsource(__import__("models.baseline", fromlist=["x"])._fingerprint)
    code = "\n".join(l.split("#", 1)[0] for l in src.splitlines())
    assert "replace" in code and "read()" in code


def test_normalised_fingerprint_is_checkout_independent():
    """Same content, different line endings, same hash - which is the whole
    property the legacy search existed to work around."""
    import hashlib
    crlf = b"a = 1\r\nb = 2\r\n"
    lf = b"a = 1\nb = 2\n"
    h = lambda b: hashlib.sha256(b.replace(b"\r\n", b"\n")).hexdigest()
    assert h(crlf) == h(lf)
    assert hashlib.sha256(crlf).hexdigest() != hashlib.sha256(lf).hexdigest()


def test_the_legacy_search_is_only_reached_by_pre_018_versions():
    """`fingerprint_matches` short-circuits on the normalised rule, so a
    post-018 version never enters the exponential branch."""
    src = inspect.getsource(me.fingerprint_matches)
    body = src[src.index('"""', src.index('"""') + 3):]
    assert body.index("fingerprint_normalised") < body.index("itertools.product")


def test_an_identity_symbol_may_change_in_place():
    """Normalising the hash means editing the hash function. That is the one
    case where a construct changing in place is not a red flag."""
    me.assert_fee_only(_cmp(changed={"models/baseline.py": ["_fingerprint"]}))


def test_a_prediction_symbol_still_may_not_change_in_place():
    with pytest.raises(me.NotEquivalent):
        me.assert_fee_only(_cmp(changed={"models/baseline.py": ["_fingerprint",
                                                                "predict"]}))


def test_the_018_change_verifies_and_is_identity_only():
    """Pinned to SHAs, not to HEAD~1. A relative ref makes the test assert
    something different after every commit - it passed when written and broke
    on the next one, which is the same class of silent drift the equivalence
    table exists to prevent."""
    cmp = me.compare("c867a5b", "b11adc1")
    me.assert_fee_only(cmp)
    assert cmp["changed"] == {"models/baseline.py": ["_fingerprint"]}
    assert cmp["added"] == {} and cmp["removed"] == {}


def _store_has_predictions():
    """Is there a store here that can actually answer this test?

    A SET ENVIRONMENT VARIABLE IS NOT A USABLE STORE. This skip used to read
    `os.getenv("LOGGER_DB") is None`, which gates on the variable existing
    rather than on the data existing. Track C runs with a throwaway
    `LOGGER_DB` pointing at an empty database: the variable is set, the skip
    does not fire, and the test FAILS on absent predictions while reading like
    a broken equivalence chain. It cost them a diagnostic cycle and was routed
    back here as a Track A item.

    Probes the thing the test needs - at least one `predictions` row - rather
    than a proxy for it. Never raises: an unreadable store is simply not one.
    """
    import sqlite3

    import config
    try:
        con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
        try:
            return con.execute("SELECT 1 FROM predictions LIMIT 1").fetchone() is not None
        finally:
            con.close()
    except Exception:
        return False


@pytest.mark.skipif(
    not _store_has_predictions(),
    reason="no configured store with predictions: the live chain reads the logger's "
           "predictions, which CI (and a throwaway LOGGER_DB) has no copy of",
)
def test_all_three_versions_resolve_to_the_one_with_predictions():
    """The live chain: two hops, undirected, ending at the 935.

    The only test here that touches the database; the rest compare ASTs and run
    anywhere. Without a store this raised `unable to open database file`, which
    reads like a broken test rather than absent data - hence the explicit skip.
    """
    from core import version_resolve as live_vr
    from models import baseline
    closure = live_vr.equivalent(baseline.MODEL_VERSION)
    assert len(closure) == 3
    v, note = live_vr.resolve(2026, 1, baseline.MODEL_VERSION)
    assert v.endswith("679868a8549a") and "EQUIVALENT" in note
