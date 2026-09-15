"""Brief 022 holdout A - run every sweep module on NFL week 2, once.

    python -m research.sweep.replicate_week2            # refuses until allowed
    python -m research.sweep.replicate_week2 --check    # say whether it would run

Refuses unless docs/briefs/022-candidates.md is committed AND every week-2 game
has a final score (research.sweep.common.open_population). Each module runs in
its own process with SWEEP_POPULATION=nfl_wk2, which swaps the fences and sends
its registry to results/<name>_wk2.jsonl with role "replication" - the week-1
search registries cannot be overwritten. Then summarize grades bar 2 against
the week-2 records named in candidates.json.
"""
import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MODULES = ("research.sweep.h1_settlement", "research.sweep.h2_imbalance",
           "research.sweep.h3_lifecycle", "research.sweep.scan")


def check():
    env = dict(os.environ, SWEEP_POPULATION="nfl_wk2")
    r = subprocess.run([sys.executable, "-c", "import research.sweep.common"],
                       cwd=ROOT, env=env, capture_output=True, text=True)
    return r.returncode == 0, (r.stderr.strip().splitlines() or ["ok"])[-1]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    ok, why = check()
    print(f"week-2 holdout: {'OPEN' if ok else 'CLOSED'} - {why}")
    if a.check or not ok:
        sys.exit(0 if ok else 2)
    env = dict(os.environ, SWEEP_POPULATION="nfl_wk2")
    for mod in MODULES:
        print(f"\n== {mod} (nfl_wk2)")
        r = subprocess.run([sys.executable, "-m", mod], cwd=ROOT, env=env)
        if r.returncode != 0:
            sys.exit(f"{mod} failed with exit {r.returncode}; nothing else run")
    subprocess.run([sys.executable, "-m", "research.sweep.summarize"], cwd=ROOT)


if __name__ == "__main__":
    main()
