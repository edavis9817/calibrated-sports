"""f-22: adversarial battery against a-57's core.record, run with the a-57 worktree on sys.path.
Every case prints ACCEPTED / EXCLUDED / REFUSED; a case marked [DEFECT] is one where the
outcome is not what the record's promise requires.

    python attack.py <a57 root> <ledger.parquet> <replay ledger.parquet> <out dir> <backtest doc> <research.json>
"""
import copy
import datetime as dt
import json
import os
import random
import sys
import time

sys.path.insert(0, sys.argv[1])
import polars as pl  # noqa: E402
from core import record as R  # noqa: E402

LED, REPLAY, OUT = sys.argv[2], sys.argv[3], sys.argv[4]


def load(path):
    rows = pl.read_parquet(path).to_dicts()
    for r in rows:              # the job's own loader does exactly this
        for k, v in r.items():
            if isinstance(v, float) and v != v:
                r[k] = None
    return rows


base = load(LED)
NOW = time.time()


def iso(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


terminal = {x["lean_id"] for x in base if x["event"] != "published"}
pub0 = next(e for e in base if e["event"] == "published" and e["lean_id"] not in terminal)
K = pub0["kickoff_ts"]
results = []


def record(name, got, ok):
    results.append({"case": name, "got": got, "ok": ok})
    print(("   " if ok else "[DEFECT] ") + f"{name}: {got}")


def case(name, mutate, expect=("EXCLUDED", "REFUSED")):
    led = copy.deepcopy(base)
    ev = next(e for e in led if e["lean_id"] == pub0["lean_id"])
    mutate(ev)
    try:
        body = R.build_published(led, NOW)
        ex = {x["lean_id"] for x in body["excluded_not_pre_kickoff"]["rows"]}
        got = "EXCLUDED" if pub0["lean_id"] in ex else "ACCEPTED"
    except Exception as e:  # noqa: BLE001
        got = f"REFUSED ({type(e).__name__}: {str(e)[:70]})"
    record(name, got, got.split()[0] in expect)


def both(v):
    return lambda e: e.update(read_at=v, event_at=v)


case("read_at = event_at = kickoff + 1s", both(iso(K + 1)))
case("read_at = event_at = kickoff + 1h", both(iso(K + 3600)))
case("read_at = event_at = exactly kickoff", both(iso(K)))
case("read_at before, event_at kickoff + 1s", lambda e: e.update(event_at=iso(K + 1)))
case("read_at null", lambda e: e.update(read_at=None))
case("event_at null", lambda e: e.update(event_at=None))
case("kickoff_ts null", lambda e: e.update(kickoff_ts=None))
case("kickoff_ts NaN (float, a direct caller), read kickoff + 1h",
     lambda e: e.update(kickoff_ts=float("nan"), read_at=iso(K + 3600), event_at=iso(K + 3600)))
case("kickoff_ts +inf, read kickoff + 1h",
     lambda e: e.update(kickoff_ts=float("inf"), read_at=iso(K + 3600), event_at=iso(K + 3600)))
case("kickoff_ts in MILLISECONDS, read kickoff + 1h",
     lambda e: e.update(kickoff_ts=K * 1000, read_at=iso(K + 3600), event_at=iso(K + 3600)))
case("kickoff_ts 0", lambda e: e.update(kickoff_ts=0.0))
day = dt.datetime.fromtimestamp(K, dt.timezone.utc).strftime("%Y-%m-%d")
case(f"read_at = event_at = date-only '{day}' (kickoff day; true write time unknown)", both(day))
naive = dt.datetime.fromtimestamp(K + 60, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
case(f"read_at = event_at = naive '{naive}' (kickoff + 60s as UTC wall time; host tz {time.strftime('%Z')})",
     both(naive))
case("read_at = event_at = unix-seconds string, kickoff + 1h", both(str(int(K + 3600))))
off = dt.datetime.fromtimestamp(K + 3600, dt.timezone(dt.timedelta(hours=5))).isoformat()
case("read_at = event_at with +05:00 offset, true instant kickoff + 1h", both(off))
case("ledger kickoff 10 min LATE vs true, read 5 min after true kickoff (two clocks)",
     lambda e: e.update(kickoff_ts=K + 600, read_at=iso(K + 300), event_at=iso(K + 300)))

# the same lean published twice, the second after kickoff
led = copy.deepcopy(base)
p = next(e for e in led if e["lean_id"] == pub0["lean_id"])
led.append(dict(p, read_at=iso(K + 60), event_at=iso(K + 60)))
try:
    R.build_published(led, NOW)
    got = "ACCEPTED"
except Exception as e:  # noqa: BLE001
    got = f"REFUSED ({str(e)[:60]})"
record("same lean published twice, second post-kickoff", got, got.startswith("REFUSED"))

# THE RECONSTRUCTION: a replayed week (board_read --at: event_at = the replay time) appended to
# the live ledger - exactly what `board_read --season 2026 --week 2 --at ... --dest BOARD_EXPORT_DIR` writes
rep = load(REPLAY)
n_rep = sum(1 for r in rep if r["event"] == "published")
spliced = base + rep
body = R.build_published(spliced, NOW)
wk = [(w["season"], w["week"], w["n"], w["graded"]) for w in body["weeks"]]
got = (f"published {body['n_published']} graded {body['n_graded']} excluded "
       f"{body['excluded_not_pre_kickoff']['n']} of {n_rep} replayed; weeks (s,w,n,graded) {wk}")
record("replayed week 2 (a-35's replay ledger) appended after the live week 3/4 rows", got,
       body["excluded_not_pre_kickoff"]["n"] >= n_rep)
iv = body["record"]["interval"]
print("    spliced interval:", json.dumps({k: iv[k] for k in ("n_blocks", "hit_rate", "margin_pp", "informative")}))
print("    spliced record:", json.dumps({k: body["record"][k] for k in ("hit_rate", "margin_pp", "units")}))
ev_order = [e["event_at"] for e in spliced]
mono = all(a <= b for a, b in zip(ev_order, ev_order[1:]))
print("    event_at non-decreasing in file order?", mono, "(False = a monotonic check catches this splice)")
ev_live = [e["event_at"] for e in base]
print("    ...and on the real ledger?", all(a <= b for a, b in zip(ev_live, ev_live[1:])))
json.dump({"spliced_interval": iv, "spliced_weeks": body["weeks"]},
          open(os.path.join(OUT, "spliced.json"), "w"), indent=1)

# two honest weeks: week 4 graded synthetically, i.e. what the NEXT real build publishes
led = copy.deepcopy(base)
rnd = random.Random(22)
for e in [x for x in base if x["event"] == "published" and x["week"] == 4]:
    led.append(dict(e, event="graded", result=rnd.choice(["cleared", "missed"]), actual=1.0,
                    event_at=iso(e["kickoff_ts"] + 4 * 3600)))
iv2 = R.build_published(led, NOW)["record"]["interval"]
record("two graded weeks (week 4 graded synthetically): interval bounds must stay null while "
       "informative is false",
       f"hit_rate {iv2['hit_rate']} margin_pp {iv2['margin_pp']} informative {iv2['informative']} "
       f"why {iv2['why']!r}", iv2["hit_rate"] is None and iv2["margin_pp"] is None)

# tier bleed: each builder fed another tier's input
bt_text = open(sys.argv[5], encoding="utf-8").read()
research = json.load(open(sys.argv[6], encoding="utf-8"))


def bleed(name, fn):
    try:
        fn()
        got = "COMPUTED"
    except Exception as e:  # noqa: BLE001
        got = f"REFUSED ({type(e).__name__}: {str(e)[:70]})"
    record(name, got, got.startswith("REFUSED"))


bleed("build_published(backtest document text)", lambda: R.build_published(bt_text, NOW))
bleed("build_published(research.json rows)", lambda: R.build_published(research["rows"], NOW))
bleed("build_research(ledger rows, [])", lambda: R.build_research(base, []))
bleed("build_research({backtest doc posing as a pre-registration}, [])",
      lambda: R.build_research({"docs/X99-preregistration.md": {"text": bt_text, "commit": "x",
                                                                "author_date": "y"}}, []))
bleed("build_backtest(ledger as JSON text)", lambda: R.build_backtest(json.dumps(base, default=str), {}))
bleed("build_backtest(research.json as text)", lambda: R.build_backtest(json.dumps(research), {}))
json.dump(results, open(os.path.join(OUT, "attack-results.json"), "w"), indent=1)
print("defects:", sum(1 for r in results if not r["ok"]), "of", len(results))
