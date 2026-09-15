"""Brief 019: the trades tape pages newest first, so a capped fetch keeps the
kickoff end and loses the entry end. These pin the window and the walk back."""
from jobs import ingest_kalshi_trades as J


class _Resp:
    def __init__(self, body):
        self.status_code, self._b = 200, body

    def json(self):
        return self._b


class _Client:
    def __init__(self):
        self.params = []

    def get(self, url, params):
        self.params.append(dict(params))
        return _Resp({"trades": [], "cursor": ""})


def test_window_reaches_the_request():
    cl = _Client()
    J.fetch_one(cl, "KXNFLGAME-26SEP13BUFHOU-BUF", window=(100.7, 900.2))
    assert cl.params[0]["min_ts"] == 100 and cl.params[0]["max_ts"] == 900


def test_no_window_sends_no_bounds():
    cl = _Client()
    J.fetch_one(cl, "KXNFLREC-X")
    assert "min_ts" not in cl.params[0] and "max_ts" not in cl.params[0]


def test_a_capped_pass_walks_back_to_the_entry_end():
    calls = []
    earliest = {1000: 700, 700: 400, 400: 250}

    def fetch(win):
        calls.append(win)
        e = earliest[win[1]]
        return 20, 20000 if e > 250 else 5000, 200, \
            "hit MAX_PAGES" if e > 250 else "", e

    pages, n, code, note = J.walk_window(fetch, (200, 1000))
    assert calls == [(200, 1000), (200, 700), (200, 400)]
    assert n == 45000 and "STILL" not in note and "rounds=3" in note


def test_a_walk_that_never_ends_says_so():
    def fetch(win):
        return 20, 20000, 200, "hit MAX_PAGES", win[1] - 1

    *_, note = J.walk_window(fetch, (0, 10 ** 9))
    assert "STILL hit MAX_PAGES" in note and f"rounds={J.MAX_ROUNDS}" in note


def test_a_pass_that_makes_no_progress_stops():
    calls = []

    def fetch(win):
        calls.append(win)
        return 20, 20000, 200, "hit MAX_PAGES", win[1]   # every print at max_ts

    J.walk_window(fetch, (0, 500))
    assert len(calls) == 1
