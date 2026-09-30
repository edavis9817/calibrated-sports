"""c-28 - the season model's walk-forward output, hashed, for the identity check.

    LOGGER_DB=<market_log.db> python -m research.c28_season_identity OUT.json

Runs `jobs.season_model.compute()` exactly as the published job does (default
sims, all seasons) and writes every deterministic part of its result - the
division walk-forward rows, fits, summary, tiebreak check, the current forecast,
and a-55's projection rows, summary and current - as canonical JSON, then prints
its sha256. c-28 lifts the game forecast out of `models/season.py`; the lift is
accepted only if this hash is identical before and after it. Timings and the
log are excluded (they are not output). Writes only OUT; the store is `mode=ro`.
"""
import hashlib
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _clean(o):
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in sorted(o.items(), key=lambda kv: str(kv[0]))}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, np.ndarray):
        return _clean(o.tolist())
    if isinstance(o, (np.floating,)):
        return repr(float(o))
    if isinstance(o, float):
        return repr(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if hasattr(o, "as_dict"):
        return _clean(o.as_dict())
    if o is None or isinstance(o, (int, str, bool)):
        return o
    return repr(o)


def canonical(res):
    cur = {k: v for k, v in res["current"].items() if k != "season_obj"}
    pcur = res["projection_current"]
    pcur = {k: v for k, v in pcur.items() if k != "season_obj"} if isinstance(pcur, dict) else pcur
    keep = {"rows": res["rows"], "fits": res["fits"], "summary": res["summary"],
            "tiebreak": res["tiebreak"], "current": cur,
            "projection_rows": res["projection_rows"],
            "projection_sigma": res["projection_sigma"],
            "projection_summary": res["projection_summary"],
            "projection_current": pcur}
    return json.dumps(_clean(keep), sort_keys=True, separators=(",", ":"))


def main():
    out = sys.argv[1]
    from jobs import season_model as S
    res = S.compute(log=lambda *a: None)
    text = canonical(res)
    if len(res["rows"]) < 1000 or not res["projection_rows"]:
        raise SystemExit("walk-forward returned %d rows - refusing to hash a stub" % len(res["rows"]))
    with open(out, "w", encoding="utf-8") as f:
        f.write(text)
    print("rows %d  projection_rows %d  bytes %d  sha256 %s"
          % (len(res["rows"]), len(res["projection_rows"]), len(text),
             hashlib.sha256(text.encode()).hexdigest()))


if __name__ == "__main__":
    main()
