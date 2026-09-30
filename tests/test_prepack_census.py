"""c-26: the pre-pack census must be able to say YES, not only zero."""
import gzip
import json
import sqlite3

from research import prepack_census as pc


def test_pack_pattern_discriminates():
    for t in ("KXNFLPREPACKSGP", "KXNFLPREPACK2ML", "KXMVENFLSINGLEGAME", "KXNFLCOMBO"):
        assert pc.PACK_RE.fullmatch(t)
    for t in ("KXNFLREC", "KXNFLSPREAD", "KXNFLGAME", "KXNCAAFSPREAD"):
        assert not pc.PACK_RE.fullmatch(t)


def test_members_survives_a_torn_member(tmp_path):
    good = [gzip.compress((json.dumps({"ts": i, "endpoint": "markets"}) + "\n").encode())
            for i in range(3)]
    torn = good[1][: len(good[1]) // 2]
    p = tmp_path / "00.jsonl.gz"
    p.write_bytes(good[0] + torn + good[2])
    lines = [l for l, bad in pc.members(str(p)) if l is not None]
    bad = sum(b for l, b in pc.members(str(p)) if l is None)
    assert len(lines) == 2 and bad == 1          # a gunzip stream would stop at 1


def _store(path, n_packs, n_events):
    c = sqlite3.connect(path)
    c.execute("create table markets(venue,market_id,event_id,market_type,title,first_seen,last_seen)")
    c.execute("create table quotes(venue,market_id,event_id,ts,best_bid,best_ask)")
    c.execute("create table market_depth(venue,market_id)")
    c.execute("create table market_trades(venue,market_id)")
    c.execute("create table market_outcome(venue,market_id,unmapped_reason)")
    for i in range(n_packs):
        mid, ev = f"KXNFLPREPACKSGP-26OCT{i % n_events:02d}-{i}", f"E{i % n_events}"
        c.execute("insert into markets values('kalshi',?,?,'parlay','',0,0)", (mid, ev))
        c.execute("insert into quotes values('kalshi',?,?,1,0.2,0.3)", (mid, ev))
    c.execute("insert into quotes values('kalshi','KXNFLREC-X','E',1,0.2,0.3)")
    c.commit()
    c.close()


def test_store_census_counts_and_exit_discriminates(tmp_path):
    empty, full = str(tmp_path / "a.db"), str(tmp_path / "b.db")
    _store(empty, 0, 1)
    _store(full, 60, 9)
    e, f = pc.store_census(empty), pc.store_census(full)
    assert e["markets_prefix"] == 0 and e["two_sided_packs"] == (0, 0)
    assert f["markets_prefix"] == 60 and f["quotes_prefix"] == 60
    assert f["two_sided_packs"] == (60, 9)
    assert f["markets_any_spelling"] == 60
    fires = lambda s: s["two_sided_packs"][0] < pc.MIN_PACKS or s["two_sided_packs"][1] < pc.MIN_GAMES
    assert fires(e) and not fires(f)
