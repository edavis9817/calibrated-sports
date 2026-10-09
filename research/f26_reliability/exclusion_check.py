"""f-35: exercise select.py's self-exclusion rule against named units, in memory. READ-ONLY; writes nothing.

    python research/f26_reliability/exclusion_check.py <relay dir> f-32 f-33 f-34

For each named unit: is it excluded as the agent's own, by which half of the rule, and - the
counterfactual the listing cannot show - WOULD IT BE RANKED if its report stated an interval.
Then two plants: a non-F unit whose files_changed touch the agent directory (must NOT be
excluded: the rule is about track F), and a track-F unit that touches it (must be).
"""
import copy, importlib.util, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("f26select", os.path.join(HERE, "select.py"))
S = importlib.util.module_from_spec(spec); spec.loader.exec_module(S)
PLANT = " Planted by exclusion_check: +0.1234 [+0.0100, +0.2000]."


def main(argv):
    relay, units = argv[1], argv[2:]
    reports, bad = S.load_reports(relay)
    marker = S.load_marker(relay)
    win, scored, no_claim = S.rank(reports, marker)
    ranked = {x["unit"] for x in scored}
    excluded = {u: S.is_attack_unit(u, d) for u, d in reports.items() if S.is_attack_unit(u, d)}
    non_f = sorted(u for u in excluded if not u.startswith("f-"))
    print("%d reports; excluded as the agent's own: %s" % (len(reports), ", ".join(sorted(excluded, key=lambda u: int(u[2:])))))
    print("non-F units caught by the exclusion: %d %s" % (len(non_f), non_f))
    f_units = sorted((u for u in reports if u.startswith("f-")), key=lambda u: int(u[2:]))
    print("every track-F report in the window's time range and where the selector puts it:")
    for u in f_units:
        if reports[u]["_ts"] <= S.dt.datetime.fromisoformat(marker["window_start"]).timestamp():
            continue
        where = ("EXCLUDED (%s)" % excluded[u] if u in excluded else "has a verdict/declined" if u in marker["verdicts"] or u in marker["declined"]
                 else "RANKED AS A TARGET" if u in ranked else "in the window, no interval stated")
        print("  %-5s %s" % (u, where))
    fails = 0
    for u in units:
        if u not in reports:
            print("%s: no machine report - cannot be exercised" % u); fails += 1; continue
        why = S.is_attack_unit(u, reports[u])
        touched = [f for f in reports[u].get("files_changed") or [] if S.AGENT_DIR in f.replace("\\", "/")]
        r2 = copy.deepcopy(reports); r2[u]["summary"] = r2[u].get("summary", "") + PLANT
        would = u in {x["unit"] for x in S.rank(r2, marker)[1]}
        print("%s: excluded=%s (%s); files touching %s: %d; states an interval now: %s; WITH a planted interval it %s"
              % (u, bool(why), why or "neither half of the rule matches", S.AGENT_DIR, len(touched), u in ranked,
                 "WOULD BE RANKED AS A TARGET" if would else "would still not be ranked"))
    # plants
    cands = [u for u in win if not u.startswith("f-")]
    if not cands:
        print("no non-F unit in the window to plant on"); return 2
    c = cands[-1]
    r3 = copy.deepcopy(reports); r3[c]["files_changed"] = (r3[c].get("files_changed") or []) + [S.AGENT_DIR + "x.py"]
    got = S.is_attack_unit(c, r3[c]); ok = got is None; fails += not ok
    print("plant 1: %s (not track F) given a file under %s -> excluded=%s  (must be False)  %s" % (c, S.AGENT_DIR, bool(got), "ok" if ok else "FAIL"))
    fu = [u for u in units if u in reports and not S.is_attack_unit(u, reports[u])]
    if fu:
        r4 = copy.deepcopy(reports); r4[fu[0]]["files_changed"] = (r4[fu[0]].get("files_changed") or []) + [S.AGENT_DIR + "x.py"]
        got = S.is_attack_unit(fu[0], r4[fu[0]]); ok = got is not None; fails += not ok
        print("plant 2: %s (track F) given a file under %s -> excluded=%s  (must be True)  %s" % (fu[0], S.AGENT_DIR, bool(got), "ok" if ok else "FAIL"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
