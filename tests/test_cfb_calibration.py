"""C01 Part 2: the join, the effective sample, and the CFBD budget.

Run: pytest -q tests/test_cfb_calibration.py

Synthetic throughout - none of these touch the capture or the network. The
point is to pin behaviour that a future session would otherwise have to
rediscover by spending requests.

The budget tests exist because the failure mode is expensive and silent: 1,000
requests per calendar month, no per-call weighting, and a `for g in games:
fetch(g.id)` loop burns the month in one run while looking like ordinary code.
"""
import os
import sqlite3
import sys

import pytest

from research import cfb_calibration as cal


# =============================================================================
# name normalisation - every one of these cost a manual lookup
# =============================================================================

@pytest.mark.parametrize("a,b", [
    ("Louisiana-Monroe", "UL Monroe"),        # hyphen SEPARATES, not noise
    ("Tennessee-Martin", "UT Martin"),
    ("San José State", "San Jose St."),       # accent folding, or 'jos'
    ("UMass", "Massachusetts"),
    ("Southeastern Louisiana", "SE Louisiana"),
    ("Long Island University", "LIU"),
    ("UAlbany", "University at Albany"),
    ("Nicholls", "Nicholls St."),
    ("UT Rio Grande Valley", "UTRGV"),
    ("NC State", "North Carolina State"),
    ("Miami (FL)", "Miami"),                  # parenthetical qualifier
    ("Texas A&M", "Texas A&M"),
])
def test_the_two_feeds_agree_after_normalisation(a, b):
    assert cal.norm(a) == cal.norm(b), f"{a!r} != {b!r} -> {cal.norm(a)!r} vs {cal.norm(b)!r}"


def test_normalisation_does_not_collapse_genuinely_different_schools():
    """A normaliser aggressive enough to fix the list above must still tell
    these apart, or the join silently marries the wrong result to the wrong
    market and every calibration number is quietly wrong."""
    for a, b in [("Miami (FL)", "Miami (OH)"),
                 ("Louisiana-Monroe", "Louisiana"),
                 ("North Alabama", "Alabama"),
                 ("San Jose State", "San Diego State"),
                 ("Albany State", "Albany"),
                 ("Southern University", "South Alabama")]:
        assert cal.norm(a) != cal.norm(b), f"{a!r} and {b!r} both -> {cal.norm(a)!r}"


def test_accent_folding_happens_before_stripping():
    """NFKD first. Strip non-letters first and 'José' becomes 'jos', which
    matches nothing and drops the game without a word."""
    assert "jose" in cal.norm("San José State")


# =============================================================================
# the effective sample - the whole reason these intervals are believable
# =============================================================================

def test_wilson_is_computed_on_games_not_rungs():
    """A 19-rung ladder settles off ONE final score. Quoting the rung count
    shrinks the interval by ~sqrt(19) and manufactures significance out of one
    Saturday. Same bucket, same hit rate, twenty times the rungs - the interval
    must not move."""
    from research.longshot import wilson
    thin = [(0.5, i % 2 == 0, f"g{i}") for i in range(20)]
    fat = [(0.5, i % 2 == 0, f"g{i}") for i in range(20) for _ in range(20)]
    import io
    import contextlib

    def widths(obs):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cal.curve(obs, "x")
        return buf.getvalue()

    a, b = widths(thin), widths(fat)
    # The Wilson bracket for the 0.50 bucket must be identical in both.
    def bracket(txt):
        for ln in txt.splitlines():
            if ln.strip().startswith("0.50-0.60"):
                return ln[ln.index("["):]
        return None
    assert bracket(a) is not None
    assert bracket(a) == bracket(b), "interval narrowed when rungs were duplicated"


def test_curve_reports_both_rung_and_game_counts():
    """Both numbers must be visible or a reader cannot tell how much the
    clustering costs."""
    import io
    import contextlib
    obs = [(0.42, True, f"g{i}") for i in range(5)] + \
          [(0.42, False, "g0") for _ in range(7)]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cal.curve(obs, "x")
    line = [l for l in buf.getvalue().splitlines()
            if l.strip().startswith("0.40-0.50")][0]
    assert " 12" in line and " 5" in line       # 12 rungs over 5 games


# =============================================================================
# realized quantities
# =============================================================================

def _game(**kw):
    g = {"completed": 1, "hp": 24, "ap": 17, "nh": "home", "na": "away",
         "hq1": 7, "hq2": 3, "hq3": 7, "hq4": 7, "hot": None,
         "aq1": 0, "aq2": 10, "aq3": 0, "aq4": 7, "aot": None,
         "n_periods": 4, "home_class": "fbs", "away_class": "fcs"}
    g.update(kw)
    return g


def test_realized_totals_and_quarters():
    r = cal.realized(_game())
    assert r["total"] == 41
    assert r["quarters"] == [7, 13, 7, 14]
    assert r["reg_total"] == 41
    assert r["overtime"] is False
    assert r["by_team"] == {"home": 24, "away": 17}


def test_overtime_is_flagged_so_quarter_work_can_exclude_it():
    """OT points land in the final score and in no quarter, so a game total
    and its four quarters settle on different numbers past regulation."""
    r = cal.realized(_game(hp=31, n_periods=5, hot=7))
    assert r["overtime"] is True
    assert r["total"] == 48
    assert r["reg_total"] == 41          # regulation only
    assert r["total"] != r["reg_total"]


def test_an_unplayed_game_yields_nothing_rather_than_zeros():
    assert cal.realized(_game(completed=0)) is None
    assert cal.realized(_game(hp=None)) is None


def test_a_missing_line_score_does_not_become_a_zero_quarter():
    """A null quarter is unknown, not nil. Coercing it to 0 would drag every
    quarter mean down and read as the market overpricing quarters."""
    r = cal.realized(_game(hq3=None))
    assert r["quarters"][2] is None
    assert r["reg_total"] is None
    assert r["total"] == 41              # the final score is still known


# =============================================================================
# the budget - 1,000 a month, and the loop that would eat it
# =============================================================================

def _ledger_db(tmp_path, monkeypatch):
    """Point the job at a scratch database WITHOUT re-importing it.

    An earlier version patched `config.DB_PATH` and then popped the module so
    it would recompute `CFB_DB`. That poisons the real module: monkeypatch
    restores the attribute to whatever the freshly imported copy computed -
    which was derived from the patched config - so every later test in the
    session saw a tmp path. Patch the one attribute on the module as imported.
    """
    from jobs import ingest_cfbd as ing
    monkeypatch.setattr(ing, "CFB_DB", str(tmp_path / "cfb_probe.db"))
    return ing


def test_the_job_refuses_to_spend_below_the_reserve(tmp_path, monkeypatch):
    import config
    ing = _ledger_db(tmp_path, monkeypatch)
    c = ing.conn()
    c.execute("INSERT INTO cfbd_requests VALUES (?,?,?,?,?,?,?,?)",
              (0.0, ing.month_key(), "games", "{}", 200,
               config.CFBD_RESERVE, 10, None))
    c.commit()
    ok, why = ing.budget_ok(c)
    assert ok is False and str(config.CFBD_RESERVE) in why


def test_an_unknown_quota_is_not_an_infinite_one(tmp_path, monkeypatch):
    """Before any call the server has reported nothing. That must fall back to
    counting this month's own requests against the budget, not to 'fine'."""
    import config
    ing = _ledger_db(tmp_path, monkeypatch)
    c = ing.conn()
    assert ing.budget_ok(c)[0] is True
    spend = config.CFBD_MONTHLY_BUDGET - config.CFBD_RESERVE + 1
    c.executemany("INSERT INTO cfbd_requests VALUES (?,?,?,?,?,?,?,?)",
                  [(0.0, ing.month_key(), "games", "{}", 200, None, 1, None)
                   for _ in range(spend)])
    c.commit()
    assert ing.budget_ok(c)[0] is False


def test_every_request_is_logged_even_when_it_fails(tmp_path, monkeypatch):
    """A 401 or a 429 may still be counted by the server, so it must be
    counted by us. A ledger that only records successes drifts under the
    truth exactly when the budget is under strain."""
    import inspect
    from jobs import ingest_cfbd as ing
    src = inspect.getsource(ing.fetch_week)
    insert = src.index("INSERT INTO cfbd_requests")
    raise_at = src.index("if r.status_code != 200")
    assert insert < raise_at, "the ledger insert must precede the error exit"


def test_no_per_game_endpoint_is_reachable_from_this_job():
    """THE BUDGET FAILURE MODE. `for g in games: fetch(g.id)` is ~800 requests
    and looks like ordinary code. The job may only ever call the week-level
    endpoint, so assert that is the only URL it can build."""
    import inspect
    from jobs import ingest_cfbd as ing
    src = inspect.getsource(ing)
    code = "\n".join(l.split("#", 1)[0] for l in src.splitlines())
    assert code.count("httpx.get") == 1, "more than one request site"
    assert '/games"' in code or "/games'" in code
    for forbidden in ("/games/", "gameId", "game_id=", "/plays", "/drives",
                      "/stats/"):
        assert forbidden not in code, f"per-game endpoint reachable: {forbidden}"


def test_line_scores_survive_overtime_without_being_dropped(tmp_path, monkeypatch):
    ing = _ledger_db(tmp_path, monkeypatch)
    assert ing._periods([7, 3, 7, 7]) == (7, 3, 7, 7, None, 4)
    assert ing._periods([7, 3, 7, 7, 6]) == (7, 3, 7, 7, 6, 5)
    assert ing._periods([7, 3, 7, 7, 6, 8]) == (7, 3, 7, 7, 14, 6)
    assert ing._periods(None) == (None, None, None, None, None, 0)


def test_parse_is_idempotent(tmp_path, monkeypatch):
    """`--from-archive` re-parses every shard on every run. If that doubled
    rows the free re-derivation would be worse than useless."""
    ing = _ledger_db(tmp_path, monkeypatch)
    c = ing.conn()
    games = [{"id": 1, "season": 2026, "week": 2, "seasonType": "regular",
              "startDate": "2026-09-12T20:00:00.000Z", "completed": True,
              "homeTeam": "A", "awayTeam": "B", "homePoints": 24,
              "awayPoints": 17, "homeLineScores": [7, 3, 7, 7],
              "awayLineScores": [0, 10, 0, 7]}]
    ing.parse(c, games, quiet=True)
    ing.parse(c, games, quiet=True)
    assert c.execute("SELECT COUNT(*) FROM cfbd_games").fetchone()[0] == 1


def test_storage_comes_from_config():
    from jobs import ingest_cfbd as ing
    import config
    assert os.path.dirname(os.path.abspath(ing.CFB_DB)) == \
        os.path.dirname(os.path.abspath(config.DB_PATH))
