"""Live prices published by the logger to R2 (unit a-09).

Run: pytest -q tests/test_live_prices.py

Four things are asserted, each driven to BOTH answers:

  1. the file the logger writes matches the contract, and the key routes to
     that kind and no other;
  2. every price carries the time it was READ, not the time it was published,
     and the file carries the deadline past which it is stale;
  3. the logger's poll loop feeds the book, and a broken book cannot cost the
     loop a poll;
  4. THE DELETION TRAP, CONSTRUCTED. `export_web.upload()` deletes by absence
     inside declared prefixes and `weekly_refresh` runs it unconditionally. A
     surviving key proves nothing on its own, so each case below plants the
     condition under which the key WOULD be deleted, shows a same-shaped key
     outside `live/` IS deleted under it, and shows the `live/` key is not.
"""
import ast
import asyncio
import inspect
import json
import os

import pytest

import config
import run_logger
import store
from jobs import export_web
from jobs import publish_live_prices as L

NOW = 1_790_000_000.0          # 2026-09-21T... - any fixed instant
SITE_PREFIXES = ["nfl/market/", "nfl/players/", "nfl/teams/", "research/"]


@pytest.fixture(autouse=True)
def pinned(tmp_path, monkeypatch):
    """Nothing here may reach the live store or the real bucket."""
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setattr(config, "WEB_EXPORT_DIR", str(tmp_path / "export"))
    monkeypatch.setattr(config, "WEB_R2_BUCKET", "bucket")
    monkeypatch.setattr(config, "WEB_R2_ACCESS_KEY_ID", "x")
    monkeypatch.setattr(config, "WEB_R2_SECRET_ACCESS_KEY", "y")


def row(tk, ts, bid=0.41, ask=0.44, venue="kalshi", event=None):
    return {"venue": venue, "market_id": tk, "event_id": event or tk.rsplit("-", 1)[0],
            "best_bid": bid, "best_ask": ask, "ts": ts}


GAME = "KXNFLGAME-26SEP27BUFMIA-BUF"
GAME2 = "KXNFLGAME-26SEP27BUFMIA-MIA"
PROP = "KXNFLREC-26SEP27BUFMIA-BUFJCOOK4-4"


def book_with(*rows, every=15, markets=None):
    b = L.PriceBook(series=("KXNFLGAME",))
    b.observe(list(rows), every, markets)
    return b


# ============================================================ 1. the contract

def test_a_built_file_validates_and_its_key_routes_to_live_prices():
    b = book_with(row(GAME, NOW - 20), row(GAME2, NOW - 20),
                  markets=[{"market_id": GAME, "close_ts": NOW + 86400}])
    doc = L.build(b, now=NOW, sport="nfl")
    assert L.validate(L.key_for("nfl"), doc) is doc
    assert export_web.kind_for_key("live/nfl/prices.json") == ("live.prices", "sport")
    export_web.validate_contract({"live/nfl/prices.json": doc})     # the producer's own gate too


def test_the_contract_refuses_what_it_should():
    """The other answer. A validator that passed everything satisfies the test above."""
    doc = L.build(book_with(row(GAME, NOW - 20)), now=NOW, sport="nfl")
    for mutate in (lambda d: d.update(extra=1),
                   lambda d: d["markets"][0].update(mid=0.425),
                   lambda d: d["markets"][0].pop("read_at"),
                   lambda d: d.pop("stale_after"),
                   lambda d: d["markets"][0].update(bid=1.5)):
        bad = json.loads(json.dumps(doc))
        mutate(bad)
        with pytest.raises(L.ContractError):
            L.validate(L.key_for("nfl"), bad)


def test_the_key_must_route_to_exactly_this_kind():
    doc = L.build(book_with(row(GAME, NOW)), now=NOW, sport="nfl")
    with pytest.raises(L.ContractError):
        L.validate("live/nfl/other.json", doc)
    with pytest.raises(L.ContractError):
        L.validate("nfl/live/prices.json", doc)


def test_no_existing_key_shape_starts_resolving_as_live_prices():
    for key in ("nfl/manifest.json", "analytics/nfl/index.json", "research/execution.json",
                "nfl/teams/buf.json", "live/nfl/prices.json"):
        hits = [k["kind"] for k in export_web.CONTRACT["x-contract"]["keys"]
                if __import__("re").match(k["pattern"], key)]
        assert len(hits) <= 1, (key, hits)


# ============================================================ 2. time travels with the price

def test_read_at_is_the_read_time_not_the_publish_time():
    b = book_with(row(GAME, NOW - 540), every=600)
    b.observe([row(GAME2, NOW - 9)], 10)
    doc = L.build(b, now=NOW, sport="nfl")
    by = {m["ticker"]: m for m in doc["markets"]}
    assert by[GAME]["read_at"] == L.iso(NOW - 540)
    assert by[GAME]["expected_every_s"] == 600
    assert by[GAME2]["read_at"] == L.iso(NOW - 9)
    assert by[GAME2]["expected_every_s"] == 10
    assert doc["generated_at"] == L.iso(NOW)
    assert by[GAME]["read_at"] != doc["generated_at"]


def test_timestamps_are_floored_so_a_price_never_looks_fresher_than_it_was():
    assert L.iso(NOW + 0.999) == L.iso(NOW)


def test_an_older_read_never_overwrites_a_newer_one():
    b = book_with(row(GAME, NOW - 5, bid=0.50))
    b.observe([row(GAME, NOW - 60, bid=0.30)], 15)
    assert b.entries[GAME]["bid"] == 0.50


def test_stale_after_is_the_deadline_for_the_next_write():
    doc = L.build(book_with(row(GAME, NOW)), now=NOW, sport="nfl", every_s=15, heartbeat_s=300)
    assert doc["stale_after"] == L.iso(NOW + 300 + 15 + L.GRACE_S)
    assert doc["heartbeat_s"] == 300 and doc["publish_every_s"] == 15


def test_only_the_configured_series_and_venue_enter_the_book():
    b = book_with(row(GAME, NOW), row(PROP, NOW), row(GAME2, NOW, venue="polymarket"),
                  row("KXNFLGAMEX-1", NOW))
    assert set(b.entries) == {GAME}


def test_zero_and_one_are_not_quotes():
    b = book_with(row(GAME, NOW, bid=0, ask=1))
    doc = L.build(b, now=NOW, sport="nfl")
    assert doc["markets"][0]["bid"] is None and doc["markets"][0]["ask"] is None
    assert doc["counts"]["two_sided"] == 0


def test_the_cap_omits_the_furthest_close_and_counts_it():
    rows = [row(f"KXNFLGAME-26SEP27X{i:02d}-A", NOW) for i in range(5)]
    meta = [{"market_id": r["market_id"], "close_ts": NOW + 1000 * (5 - i)}
            for i, r in enumerate(rows)]
    doc = L.build(book_with(*rows, markets=meta), now=NOW, sport="nfl", max_markets=3)
    assert doc["counts"] == {"markets": 3, "two_sided": 3, "omitted": 2}
    kept = {m["ticker"] for m in doc["markets"]}
    assert kept == {rows[i]["market_id"] for i in (2, 3, 4)}     # nearest closes


def test_a_market_not_read_for_the_keep_window_leaves_the_file():
    b = book_with(row(GAME, NOW - 7 * 3600), row(GAME2, NOW - 60))
    assert b.prune(NOW, keep_s=6 * 3600) == 1
    assert set(b.entries) == {GAME2}


def test_in_catalogue_is_unknown_then_true_then_false():
    b = book_with(row(GAME, NOW))
    assert L.build(b, now=NOW, sport="nfl")["markets"][0]["in_catalogue"] is None
    b.set_catalogue([{"market_id": GAME}])
    assert L.build(b, now=NOW, sport="nfl")["markets"][0]["in_catalogue"] is True
    b.set_catalogue([{"market_id": GAME2}])
    assert L.build(b, now=NOW, sport="nfl")["markets"][0]["in_catalogue"] is False


def test_observe_swallows_a_malformed_row():
    b = L.PriceBook(series=("KXNFLGAME",))
    assert b.observe([{"venue": "kalshi", "market_id": GAME, "ts": "not a time"},
                      None, 7, row(GAME2, NOW)], 15) == 1
    assert set(b.entries) == {GAME2}


# ============================================================ the publisher

class FakeR2:
    def __init__(self, fail=False):
        self.objects, self.put, self.deleted, self.meta = {}, [], [], {}
        self.fail = fail

    def put_object(self, Bucket=None, Key=None, Body=None, **kw):
        if self.fail:
            raise ConnectionError("r2 down")
        self.objects[Key] = Body
        self.put.append(Key)
        self.meta[Key] = kw

    def delete_object(self, Bucket=None, Key=None):
        self.objects.pop(Key, None)
        self.deleted.append(Key)

    def get_object(self, Bucket=None, Key=None):
        raise RuntimeError("no remote state")

    def list_objects_v2(self, **kw):
        return {"Contents": [{"Key": k} for k in self.objects]}


def test_publish_writes_exactly_one_key_and_reports_no_deletion():
    r2 = FakeR2()
    pub = L.Publisher(book_with(row(GAME, NOW - 3)), client=r2, bucket="b", sport="nfl",
                      every_s=15, heartbeat_s=300)
    res = pub.publish(now=NOW)
    assert r2.put == ["live/nfl/prices.json"] and r2.deleted == []
    assert res["declared_prefixes"] == ["live/nfl/"]
    assert res["deleted"] == 0 and res["removed_withheld"] == 0
    assert r2.meta["live/nfl/prices.json"]["CacheControl"] == "no-store"
    body = json.loads(r2.objects["live/nfl/prices.json"])
    assert body["markets"][0]["read_at"] == L.iso(NOW - 3)


def test_due_on_a_newer_read_on_the_heartbeat_and_not_otherwise():
    b = book_with(row(GAME, NOW - 3))
    pub = L.Publisher(b, client=FakeR2(), bucket="b", sport="nfl", every_s=15, heartbeat_s=300)
    assert pub.due(NOW) == "newer read"
    pub.publish(now=NOW)
    assert pub.due(NOW + 5) is None                     # inside the cadence
    assert pub.due(NOW + 20) is None                    # nothing new, heartbeat not due
    b.observe([row(GAME, NOW + 18)], 15)
    assert pub.due(NOW + 20) == "newer read"
    pub.publish(now=NOW + 20)
    assert pub.due(NOW + 20 + 300) == "heartbeat"


def test_the_writer_has_no_delete_path_at_all():
    """By AST, not by grep: the docstring talks about deletion by design."""
    tree = ast.parse(inspect.getsource(L))
    calls = {n.func.attr for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "put_object" in calls                         # the walk sees calls at all
    assert not calls & {"delete_object", "delete_objects", "sync_keys", "upload"}, calls


def test_the_writer_never_touches_the_batch_uploaders_state():
    src = inspect.getsource(L)
    tree = ast.parse(src)
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not names & {"STATE_FILE", "REMOTE_STATE_KEY", "_save_state", "load_upload_state"}


def test_publishing_is_off_by_default():
    import subprocess
    import sys
    env = {k: v for k, v in os.environ.items() if k != "LIVE_PRICES_ENABLED"}
    out = subprocess.run([sys.executable, "-c", "import config; print(config.LIVE_PRICES_ENABLED)"],
                         cwd=export_web.ROOT, env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False"


def test_the_worker_returns_immediately_when_disabled(monkeypatch):
    monkeypatch.setattr(config, "LIVE_PRICES_ENABLED", False)
    r2 = FakeR2()
    pub = L.Publisher(book_with(row(GAME, NOW)), client=r2, bucket="b", sport="nfl")

    async def go():
        monkeypatch.setattr(run_logger, "_stop", asyncio.Event())    # never set
        await asyncio.wait_for(run_logger.live_prices_worker(pub), timeout=2)
    asyncio.run(go())
    assert r2.put == []


def test_the_worker_publishes_when_enabled_and_survives_a_failing_r2(monkeypatch):
    store.init_db()
    monkeypatch.setattr(config, "LIVE_PRICES_ENABLED", True)
    monkeypatch.setattr(config, "LOOP_TICK", 0.01)
    for client, expect_put in ((FakeR2(), True), (FakeR2(fail=True), False)):
        pub = L.Publisher(book_with(row(GAME, NOW)), client=client, bucket="b", sport="nfl",
                          every_s=15, heartbeat_s=300)

        async def go():
            # A fresh Event per loop: the module's own is bound to whichever
            # loop first waited on it.
            stop = asyncio.Event()
            monkeypatch.setattr(run_logger, "_stop", stop)
            task = asyncio.create_task(run_logger.live_prices_worker(pub))
            await asyncio.sleep(0.3)
            assert not task.done()                    # a failing R2 did not kill it
            stop.set()
            await asyncio.wait_for(task, timeout=2)
        asyncio.run(go())
        assert bool(client.put) is expect_put
        with store.db() as c:
            ok = c.execute("SELECT ok FROM source_health WHERE source='live_prices'").fetchone()[0]
        assert ok == int(expect_put)


# ============================================================ 3. the poll loop feeds it

class FakeVenue:
    name = "kalshi"

    def __init__(self, rows):
        self.rows = rows

    async def fetch_quotes(self, subset):
        return self.rows


def test_poll_venue_feeds_the_book_with_the_tier_cadence(monkeypatch):
    store.init_db()
    book = L.PriceBook(series=("KXNFLGAME",))
    monkeypatch.setattr(run_logger, "live_book", book)
    rows = [dict(row(GAME, NOW), sport="nfl", market_type="moneyline", side="yes", mid=0.425)]
    markets = [{"venue": "kalshi", "market_id": GAME, "market_type": "moneyline",
                "close_ts": NOW + 3600}]
    monkeypatch.setattr(run_logger, "tier_for", lambda m, k=None, now=None: "hot")
    asyncio.run(run_logger.poll_venue(FakeVenue(rows), markets, "hot", None, 15))
    assert book.entries[GAME]["every_s"] == 15
    assert book.entries[GAME]["close_ts"] == NOW + 3600


def test_a_broken_book_cannot_cost_the_poll(monkeypatch):
    store.init_db()

    class Broken(L.PriceBook):
        def observe(self, *a, **k):
            raise RuntimeError("boom")
    monkeypatch.setattr(run_logger, "live_book", Broken())
    tk = "KXNFLGAME-26SEP27DETGB-DET"   # unique: write_quotes dedupes on module state
    rows = [dict(row(tk, NOW), sport="nfl", market_type="moneyline", side="yes", mid=0.425)]
    monkeypatch.setattr(run_logger, "tier_for", lambda m, k=None, now=None: "hot")
    asyncio.run(run_logger.poll_venue(FakeVenue(rows), [{"venue": "kalshi", "market_id": tk}],
                                      "hot", None, 15))
    with store.db() as c:
        n = c.execute("SELECT COUNT(*) FROM quotes WHERE market_id = ?", (tk,)).fetchone()[0]
        ok = c.execute("SELECT ok FROM poll_log ORDER BY ts DESC LIMIT 1").fetchone()[0]
    assert n == 1 and ok == 1


def test_the_venue_worker_passes_each_tiers_cadence():
    """By AST: the only poll_venue call in venue_worker passes the cadence."""
    tree = ast.parse(inspect.getsource(run_logger.venue_worker))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "id", None) == "poll_venue"]
    assert len(calls) == 1 and len(calls[0].args) == 5, ast.dump(calls[0])


# ============================================================ 4. the deletion trap, constructed

LIVE_KEY = "live/nfl/prices.json"


def _export_tree(tmp_path, state):
    root = tmp_path / "export"
    for key in ("nfl/teams/cin.json",):
        p = root.joinpath(*key.split("/"))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('{"k": 1}', encoding="utf-8")
    (root / export_web.STATE_FILE).write_text(json.dumps(state), encoding="utf-8")
    return str(root)


def _upload(dest, refreshed, bucket):
    return export_web.upload(dest=dest, client=bucket, refreshed=refreshed, log=lambda *a, **k: None)


def test_TRAP_1_the_ordinary_case_the_live_key_is_invisible_to_the_batch_uploader(tmp_path):
    """The logger PUTs the key; the weekly upload runs with every site prefix
    declared. The live key is not in the upload record, so it cannot be
    computed as removed - and a stale SITE key under a declared prefix, the
    contrast, IS removed in the same run, so the delete path demonstrably fired."""
    bucket = FakeR2()
    L.Publisher(book_with(row(GAME, NOW)), client=bucket, bucket="bucket",
                sport="nfl").publish(now=NOW)
    dest = _export_tree(tmp_path, {"nfl/teams/cin.json": "old", "nfl/teams/gone.json": "x"})
    bucket.objects["nfl/teams/gone.json"] = b"{}"
    res = _upload(dest, SITE_PREFIXES, bucket)
    assert "nfl/teams/gone.json" in bucket.deleted                 # the path is live
    assert LIVE_KEY in bucket.objects and LIVE_KEY not in bucket.deleted
    assert res["removed_withheld"] == 0


def test_TRAP_2_a_poisoned_record_that_remembers_the_live_key_is_withheld_not_deleted(tmp_path):
    """The dangerous case, planted: the upload record CONTAINS the live key
    (someone once uploaded a local copy) and it is absent locally. It is
    withheld and COUNTED, under the prefix that names it.

    WHAT PROTECTS IT HERE IS THE EXISTING DECLARATION SCOPING, NOT THE a-09
    GUARD - measured: with `export_web.LIVE_PREFIX` mutated away this test still
    passes, because no site prefix reaches `live/`. The case the scoping cannot
    cover is a declaration that DOES reach it; that is TRAP_3, which fails under
    the same mutation."""
    bucket = FakeR2()
    bucket.objects[LIVE_KEY] = b"{}"
    bucket.objects["nfl/teams/gone.json"] = b"{}"
    dest = _export_tree(tmp_path, {"nfl/teams/cin.json": "old", "nfl/teams/gone.json": "x",
                                   LIVE_KEY: "x"})
    res = _upload(dest, SITE_PREFIXES, bucket)
    assert "nfl/teams/gone.json" in bucket.deleted                 # contrast: same shape, deleted
    assert LIVE_KEY not in bucket.deleted and LIVE_KEY in bucket.objects
    assert res["removed_withheld"] == 1
    assert res["withheld_prefixes"] == ["live/"]


@pytest.mark.parametrize("declared", [["live/"], ["live/nfl/"], ["l"], [""], SITE_PREFIXES + ["live/"]])
def test_TRAP_3_a_declaration_reaching_live_is_refused_outright(tmp_path, declared):
    """Refused, not narrowed: a caller that believes it owns `live/` has a
    wrong model. Every shape - exact, inside, containing, empty - raises, and
    nothing is written or deleted first."""
    bucket = FakeR2()
    bucket.objects[LIVE_KEY] = b"{}"
    dest = _export_tree(tmp_path, {LIVE_KEY: "x"})
    with pytest.raises(ValueError, match="live/"):
        _upload(dest, declared, bucket)
    assert bucket.put == [] and bucket.deleted == []


def test_TRAP_3b_the_guard_is_what_refuses__an_unrelated_prefix_is_accepted(tmp_path):
    """The other answer: a declaration that does NOT reach live/ still works."""
    bucket = FakeR2()
    dest = _export_tree(tmp_path, {})
    res = _upload(dest, ["nfl/teams/", "analytics/"], bucket)
    assert res["configured"] is True


def test_TRAP_4_a_stale_local_live_file_is_never_uploaded_over_the_fresh_one(tmp_path):
    bucket = FakeR2()
    bucket.objects[LIVE_KEY] = b'{"fresh": true}'
    dest = _export_tree(tmp_path, {})
    p = os.path.join(dest, "live", "nfl")
    os.makedirs(p)
    with open(os.path.join(p, "prices.json"), "w", encoding="utf-8") as f:
        f.write('{"stale": true}')
    res = _upload(dest, SITE_PREFIXES, bucket)
    assert LIVE_KEY not in bucket.put
    assert bucket.objects[LIVE_KEY] == b'{"fresh": true}'
    assert res["live_skipped"] == 1
    assert "nfl/teams/cin.json" in bucket.put                      # the upload itself ran


def test_TRAP_5_no_sync_keys_prefix_in_the_producer_reaches_live():
    from tests.test_prefix_ownership import containment_violations, owned_prefixes
    prefixes, dynamic = owned_prefixes()
    assert dynamic == 0 and len(prefixes) >= 3
    assert containment_violations(prefixes, foreign=L.LIVE_PREFIX) == []
    assert containment_violations(["live/nfl/"], foreign=L.LIVE_PREFIX)   # it can fire
