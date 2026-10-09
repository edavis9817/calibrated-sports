"""A pytest plugin that writes down what every `prune_quotes.run` returned (a-84).

    A84_PRUNE_LOG=prunes.jsonl python -m pytest -q -p research.lock_hold_plugin

A passing lock test prints nothing, so a suite run says whether `max_batch_s`
stayed under the bound and not what it was. This records it, with the test that
asked, for every prune in the run. It only looks: the wrapped function is called
with the caller's own arguments and its return value is handed back untouched.
Without A84_PRUNE_LOG it does nothing at all.
"""
import functools
import json
import os
import time


def pytest_configure(config):
    path = os.environ.get("A84_PRUNE_LOG")
    if not path:
        return
    from jobs import prune_quotes
    real = prune_quotes.run

    @functools.wraps(real)
    def run(*a, **k):
        s = real(*a, **k)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "test": os.environ.get("PYTEST_CURRENT_TEST", ""),
                "max_batch_s": s["max_batch_s"], "delete_s": s["delete_s"],
                "batches": s["batches"], "deleted": s["deleted"],
                "at": round(time.time(), 1)}) + "\n")
        return s

    prune_quotes.run = run
