"""model_version must be derived from the code. Run: pytest -q

THE BUG. `baseline-usage-0.2` was written by TWO different builds - fingerprints
24e5b62d5819 and c4479a8b0627, 1,049 rows each. The paper ledger then averaged
171 tickets from one build and 161 from the other into a single number that
described neither model, and nothing in the store could tell them apart because
both rows carried the same label.

A typed version drifts from its code silently. A derived one cannot.
"""
import pytest

import store
from models import baseline


def _row(version, fingerprint, oid="o1"):
    return {"outcome_id": oid, "model_version": version,
            "code_fingerprint": fingerprint, "as_of_ts": 1.0,
            "created_ts": 1.0, "family": "negative_binomial",
            "params_json": "{}", "mean": 1.0, "prob_over": 0.5,
            "push_prob": 0.0, "prior_games": 10, "shrink_weight": 0.5}


def test_the_version_embeds_the_fingerprint():
    v = baseline.model_version()
    assert v.startswith("baseline-usage-0.4+")
    assert baseline._fingerprint() in v
    assert len(baseline._fingerprint()) == 12


def test_the_fingerprint_covers_the_model_and_not_the_logger():
    """It must change when what the model predicts changes, and only then. A
    venue-adapter edit changing the model's version is noise; a shrinkage
    constant changing it silently is the failure."""
    import inspect
    src = inspect.getsource(baseline._fingerprint)
    assert "models/baseline.py" in src
    assert "models/features.py" in src
    assert "core/distributions.py" in src
    assert "run_logger" not in src and "venues/" not in src


def test_writing_a_prediction_whose_version_disagrees_is_refused(tmp_path,
                                                                monkeypatch):
    import config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    store.init_db()

    with pytest.raises(store.VersionMismatch) as e:
        store.record_prediction(_row("baseline-usage-0.2", "c4479a8b0627"))
    assert "does not embed" in str(e.value)

    # The exact historical shape: same label, two different builds.
    store.record_prediction(_row("baseline-usage-0.2+24e5b62d5819",
                                 "24e5b62d5819", oid="o1"))
    with pytest.raises(store.VersionMismatch):
        store.record_prediction(_row("baseline-usage-0.2", "c4479a8b0627",
                                     oid="o2"))


def test_a_missing_fingerprint_is_refused_too(tmp_path, monkeypatch):
    """Not merely mismatched - absent. An empty fingerprint would otherwise
    pass a naive substring check against any version string."""
    import config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    store.init_db()
    with pytest.raises(store.VersionMismatch):
        store.record_prediction(_row("baseline-usage-0.4+abc", ""))


def test_a_derived_version_round_trips(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    store.init_db()
    pid = store.record_prediction(
        _row(baseline.model_version(), baseline._fingerprint()))
    assert pid
    with store.db() as c:
        v, f = c.execute("SELECT model_version, code_fingerprint FROM "
                         "predictions WHERE prediction_id=?", (pid,)).fetchone()
    assert f in v
