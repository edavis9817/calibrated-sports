"""f-21 part 3 probe, copied into an a-31 checkout as tests/test_f21_gate.py by
research/f21_plant_board_gate.py. One Board read on a fixture store; records whether the
source gate refused, what it approved for board_read, and what SQLite saw on the watched
connection. Asserts nothing about the plant: the record is the result."""
import json
import os

from jobs import source_registry as R
from tests.test_board import T0, H, env, read, snapshot  # noqa: F401


def test_probe(env):
    snapshot(T0 - H, "ev-2026_03_NYJ_DET",
             [("player_receptions", "Amon-Ra St. Brown", 7.5, -105, -115, None)])
    rec = {"plant": os.environ.get("F21_PLANT"), "problems_at_import": R.PROBLEMS["nfl"],
           "declared_board_read": list(R.DECLARED["nfl"].get("board_read", ()))}
    try:
        s = env["J"].run(2026, 3, env["dest"], read_ts=T0, log=lambda *_: None)
        rec.update(refused=False, tables_read=s["tables_read"],
                   approved_board_read=s["sources"].get("nfl/board_read"),
                   wrote=os.path.exists(env["J"].ledger_path(env["dest"])))
    except Exception as e:  # noqa: BLE001 - the verdict is the record
        rec.update(refused=True, error=f"{type(e).__name__}: {str(e)[:400]}",
                   wrote=os.path.exists(env["J"].ledger_path(env["dest"])))
    finally:
        R._READS.pop("nfl", None)
    with open(os.environ["F21_GATE_OUT"], "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=1)
