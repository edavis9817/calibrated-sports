"""f-30: c-39's hash claim, and whether it could have failed.

Runs a target tree's research.c28_season_identity on the live store (mode=ro), prints the
sha256, then lists every file of that tree the run actually IMPORTED (sys.modules, not a
hand list - f-29 named that gap) and intersects it with the files changed between two
commits. An empty intersection means the hash could not have moved for this change.

    python research/f30_cfb_build/identity.py <worktree> --store <the NFL store> [--plant] [--diff BASE HEAD]
"""
import hashlib, os, subprocess, sys, time
src = os.path.abspath(sys.argv[1])
plant = "--plant" in sys.argv
store = os.path.abspath(sys.argv[sys.argv.index("--store") + 1])   # passed, never a literal (tests/test_storage_paths)
assert os.path.isfile(store), store
os.environ["LOGGER_DB"] = store
os.chdir(src); sys.path.insert(0, src)
import config
from research import c28_season_identity as I
from jobs import season_model as S
from models import game as G
assert os.path.abspath(S.__file__).startswith(src), S.__file__
assert os.path.abspath(config.DB_PATH) == store, config.DB_PATH
if plant:
    G.MOV_A = G.MOV_A + 0.1
t = time.time()
res = S.compute(log=lambda *a: None)
text = I.canonical(res)
assert len(res["rows"]) > 1000, len(res["rows"])
print("%s plant=%s rows %d projection_rows %d bytes %d sha256 %s (%.0fs)" % (src, plant, len(res["rows"]), len(res["projection_rows"]), len(text), hashlib.sha256(text.encode()).hexdigest(), time.time() - t), flush=True)
loaded = sorted({os.path.relpath(os.path.abspath(m.__file__), src).replace("\\", "/") for m in list(sys.modules.values())
                 if getattr(m, "__file__", None) and os.path.abspath(m.__file__).startswith(src + os.sep)})
print("imported from the tree: %d files" % len(loaded))
if "--diff" in sys.argv:
    i = sys.argv.index("--diff")
    changed = subprocess.run(["git", "diff", "--name-status", sys.argv[i + 1], sys.argv[i + 2]], capture_output=True, text=True, check=True).stdout.split("\n")
    changed = [c.split("\t") for c in changed if c.strip()]
    assert changed, "empty diff - wrong commits"
    hit = [c for c in changed if c[-1] in loaded]
    print("changed %s..%s: %d files (%s); of those imported by the hashed run: %d %s" % (
        sys.argv[i + 1], sys.argv[i + 2], len(changed), ", ".join(sorted({c[0] for c in changed})), len(hit), hit))
    print("imported: " + " ".join(loaded))
