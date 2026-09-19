"""The market vocabulary, and the guard that was blind to it.

Run: pytest -q tests/test_market_definitions.py

`prop_history` names MARKETS (`receptions`, `anytime_td`); the rest of the export
names STATS (`rec`, `td`). They are different claims and the producer already
spelled them differently. Nothing validated the market half: `stat_keys_used` has
five branches and none descends into `prop_history`, so `assert_stats_defined`
accepted anything there while raising on the same key one field away.

Every test below has to be able to return the other answer, because a guard that
cannot fail is not a guard — which is the class this file exists to close.
"""
import json
import os

import pytest

from jobs import export_web as E


# ------------------------------------------------ the table describes reality

def test_every_market_definition_has_a_label_and_a_stat_field():
    assert E.MARKET_DEFINITIONS, "an empty table would pass every test below"
    for key, d in E.MARKET_DEFINITIONS.items():
        assert d["label"], key
        assert "stat" in d, f"{key} must state its stat, even when that is None"
        assert set(d) <= {"label", "stat", "note"}, f"{key} carries an unknown field"


def test_a_non_null_stat_points_at_a_REAL_stat_definition():
    """The whole point of the field. A market pointing at a key the manifest does
    not define would send the site looking up a label that is not there."""
    for key, d in E.MARKET_DEFINITIONS.items():
        if d["stat"] is not None:
            assert d["stat"] in E.STAT_DEFINITIONS, f"{key} -> {d['stat']} is not defined"


def test_every_null_stat_EXPLAINS_ITSELF():
    """A null without a reason is indistinguishable from an unfinished row, and
    the next person to read it will helpfully fill it in."""
    for key, d in E.MARKET_DEFINITIONS.items():
        if d["stat"] is None:
            assert d.get("note"), f"{key} has a null stat and no note saying why"


def test_the_table_is_not_trivially_all_null_or_all_mapped():
    """Both states must actually occur, or the nullable field is decoration."""
    stats = [d["stat"] for d in E.MARKET_DEFINITIONS.values()]
    assert any(s is None for s in stats), "no null - the nullability is unexercised"
    assert any(s is not None for s in stats), "nothing mapped - the field says nothing"


def test_anytime_td_is_NOT_mapped_to_the_td_count():
    """The specific false equivalence this design refuses. `td` is a count; the
    market is 'did he score at all'. They are different questions."""
    assert E.MARKET_DEFINITIONS["anytime_td"]["stat"] is None
    assert "td" in E.STAT_DEFINITIONS, "the tempting wrong answer must exist, or this proves nothing"


# ------------------------------------------------------------- the guard works

def _summary(stats=(), records=()):
    return {
        "kind": "player_summary",
        "prop_history": {
            "stats": [{"stat": s} for s in stats],
            "records": [{"stat": s} for s in records],
        },
    }


def test_market_keys_used_reads_prop_history():
    used = E.market_keys_used(_summary(stats=["receptions"], records=["anytime_td"]))
    assert used == {"receptions", "anytime_td"}


def test_market_keys_used_ignores_kinds_that_carry_no_history():
    assert E.market_keys_used({"kind": "team", "splits": []}) == set()
    assert E.market_keys_used({"kind": "player_summary", "prop_history": None}) == set()


def test_an_undefined_market_key_RAISES():
    with pytest.raises(E.MarketDefinitionError) as e:
        E.assert_markets_defined({"k": _summary(stats=["not_a_market"])}, E.MARKET_DEFINITIONS)
    assert "not_a_market" in str(e.value)


def test_a_defined_market_key_passes():
    """The other answer on the other input. Without this a guard that always
    raised would satisfy the test above."""
    E.assert_markets_defined({"k": _summary(stats=["receptions"], records=["receptions"])},
                             E.MARKET_DEFINITIONS)


def test_the_RECORDS_half_is_checked_too():
    """`records` outnumber `stats` roughly 38 to 1, so a guard reading only
    `stats` would walk past most of the published surface."""
    with pytest.raises(E.MarketDefinitionError):
        E.assert_markets_defined({"k": _summary(records=["bogus_market"])}, E.MARKET_DEFINITIONS)


def test_the_OLD_guard_still_does_not_cover_this_and_that_is_why_this_one_exists():
    """Pinning the gap rather than assuming it closed itself.

    If someone later teaches `stat_keys_used` to walk `prop_history`, this fails
    and the reader is told to reconsider which vocabulary that shape belongs to —
    market names are not stat keys and validating them against
    `STAT_DEFINITIONS` would reject every one of them.
    """
    assert E.stat_keys_used(_summary(stats=["receptions"])) == set()


# ------------------------------------------------ it reaches the real manifest

def test_the_manifest_carries_the_table_and_the_contract_requires_it():
    with open(os.path.join(E.ROOT, "web", "contract", "v2", "contract.schema.json"),
              encoding="utf-8") as f:
        contract = json.load(f)
    manifest = contract["$defs"]["SportManifest"]
    assert "market_definitions" in manifest["properties"]
    assert "market_definitions" in manifest["required"], (
        "if it is not required, an export that forgets it still validates")
    md = contract["$defs"]["MarketDefinition"]
    assert md["properties"]["stat"]["type"] == ["string", "null"]
    assert md["additionalProperties"] is False
