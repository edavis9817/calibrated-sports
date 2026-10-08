"""f-29: c-39's hash claim. Runs a target tree's research.c28_season_identity on the live store (mode=ro)
and prints the sha256; `plant` moves models.game.MOV_A by 0.1 first, to show the hash CAN move.
    python research/f26_reliability/identity_c39.py <worktree> [plant]
The NFL store is read from F26_NFL_STORE, never a literal (tests/test_storage_paths; f-30)."""
import hashlib, os, sys, time
src, plant = sys.argv[1], len(sys.argv) > 2 and sys.argv[2] == "plant"
store = os.path.abspath(os.environ["F26_NFL_STORE"])
assert os.path.isfile(store), store
os.environ["LOGGER_DB"] = store
os.chdir(src); sys.path.insert(0, os.path.abspath(src))
import config
from research import c28_season_identity as I
from jobs import season_model as S
from models import game as G
assert os.path.abspath(S.__file__).startswith(os.path.abspath(src)), S.__file__
assert os.path.abspath(config.DB_PATH) == store, config.DB_PATH
if plant:
    G.MOV_A = G.MOV_A + 0.1
t = time.time()
res = S.compute(log=lambda *a: None)
text = I.canonical(res)
print("%s plant=%s rows %d projection_rows %d bytes %d sha256 %s (%.0fs)" % (src, plant, len(res["rows"]), len(res["projection_rows"]), len(text), hashlib.sha256(text.encode()).hexdigest(), time.time() - t), flush=True)
